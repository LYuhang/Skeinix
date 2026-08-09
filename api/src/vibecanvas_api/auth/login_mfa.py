"""Password-gated, pre-Session MFA challenge primitives.

This module deliberately keeps login challenges separate from ``sessions``:
proving a password never creates an ambient application credential when an
account has MFA enabled.  Only a successful second factor may create the
Session used by business routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import uuid

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import base64url_to_bytes, generate_authentication_options
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    UserVerificationRequirement,
)

from vibecanvas_api.auth.tokens import hash_token, new_token
from vibecanvas_api.config import config
from vibecanvas_api.storage.models import (
    UserLoginMfaChallenge,
    UserMfaTotp,
    UserWebAuthnCredential,
)


LOGIN_MFA_TTL = timedelta(minutes=5)
WEBAUTHN_TIMEOUT_MS = 120_000
MAX_FAILED_ATTEMPTS = 5


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class LoginMfaIssue:
    raw_token: str
    methods: tuple[str, ...]
    webauthn_options: dict | None
    expires_at: datetime


def credential_descriptor(
    row: UserWebAuthnCredential,
) -> PublicKeyCredentialDescriptor:
    transports: list[AuthenticatorTransport] = []
    for raw in row.transports:
        try:
            transports.append(AuthenticatorTransport(raw))
        except ValueError:
            continue
    return PublicKeyCredentialDescriptor(
        id=base64url_to_bytes(row.credential_id),
        transports=transports or None,
    )


def canonical_credential_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise HTTPException(400, "webauthn_credential_invalid")
    try:
        return bytes_to_base64url(base64url_to_bytes(value))
    except Exception:
        raise HTTPException(400, "webauthn_credential_invalid") from None


def authentication_options(
    rows: list[UserWebAuthnCredential],
) -> tuple[bytes, dict]:
    options = generate_authentication_options(
        rp_id=config.webauthn_rp_id,
        timeout=WEBAUTHN_TIMEOUT_MS,
        allow_credentials=[credential_descriptor(row) for row in rows],
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    return options.challenge, json.loads(options_to_json(options))


async def issue_login_mfa_challenge(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    audience: str,
) -> LoginMfaIssue | None:
    """Return ``None`` when no active factor exists, else create one challenge."""
    totp = await session.get(UserMfaTotp, user_id)
    credentials = list(
        (
            await session.execute(
                select(UserWebAuthnCredential).where(
                    UserWebAuthnCredential.user_id == user_id
                )
            )
        ).scalars()
    )
    methods: list[str] = []
    webauthn_options: dict | None = None
    webauthn_challenge: bytes | None = None
    if credentials:
        methods.append("webauthn")
        webauthn_challenge, webauthn_options = authentication_options(credentials)
    if totp is not None and totp.status == "active":
        methods.append("totp")
        if totp.recovery_code_hashes:
            methods.append("recovery")
    if not methods:
        return None

    # A new password proof replaces older unfinished proofs for the same
    # account/audience. This bounds the number of live factor attempts without
    # constraining ordinary multi-device Sessions after authentication.
    await session.execute(
        delete(UserLoginMfaChallenge).where(
            UserLoginMfaChallenge.expires_at <= _now()
        )
    )
    await session.execute(
        delete(UserLoginMfaChallenge).where(
            UserLoginMfaChallenge.user_id == user_id,
            UserLoginMfaChallenge.audience == audience,
        )
    )
    raw_token, token_hash = new_token()
    expires_at = _now() + LOGIN_MFA_TTL
    session.add(
        UserLoginMfaChallenge(
            token_hash=token_hash,
            user_id=user_id,
            tenant_id=tenant_id,
            audience=audience,
            available_methods=methods,
            webauthn_challenge=webauthn_challenge,
            expires_at=expires_at,
        )
    )
    await session.flush()
    return LoginMfaIssue(
        raw_token=raw_token,
        methods=tuple(methods),
        webauthn_options=webauthn_options,
        expires_at=expires_at,
    )


async def locked_login_challenge(
    session: AsyncSession,
    raw_token: str,
) -> UserLoginMfaChallenge:
    if len(raw_token) < 32 or len(raw_token) > 512:
        raise HTTPException(400, "login_mfa_challenge_invalid_or_expired")
    row = (
        await session.execute(
            select(UserLoginMfaChallenge)
            .where(UserLoginMfaChallenge.token_hash == hash_token(raw_token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if (
        row is None
        or row.expires_at <= _now()
        or row.failed_attempts >= MAX_FAILED_ATTEMPTS
    ):
        if row is not None:
            await session.delete(row)
        raise HTTPException(400, "login_mfa_challenge_invalid_or_expired")
    return row


async def record_failed_factor(
    session: AsyncSession,
    row: UserLoginMfaChallenge,
) -> None:
    row.failed_attempts += 1
    if row.failed_attempts >= MAX_FAILED_ATTEMPTS:
        await session.delete(row)


async def refresh_webauthn_options(
    session: AsyncSession,
    row: UserLoginMfaChallenge,
) -> dict:
    if "webauthn" not in row.available_methods:
        raise HTTPException(409, "webauthn_not_enrolled")
    credentials = list(
        (
            await session.execute(
                select(UserWebAuthnCredential).where(
                    UserWebAuthnCredential.user_id == row.user_id
                )
            )
        ).scalars()
    )
    if not credentials:
        raise HTTPException(409, "webauthn_not_enrolled")
    challenge, options = authentication_options(credentials)
    row.webauthn_challenge = challenge
    return options
