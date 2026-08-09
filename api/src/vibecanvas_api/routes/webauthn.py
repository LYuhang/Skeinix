"""Phishing-resistant WebAuthn enrollment and high-risk step-up."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import bytes_to_base64url, options_to_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from vibecanvas_api.audit import actions
from vibecanvas_api.audit.context import extract_request_audit_context
from vibecanvas_api.audit.service import record_auth_audit
from vibecanvas_api.auth.deps import (
    AuthContext,
    current_user,
    require_recent_step_up,
)
from vibecanvas_api.auth.password import verify_password
from vibecanvas_api.auth.ratelimit import (
    LoginRateLimitExceeded,
    LoginRateLimitUnavailable,
    begin_login_attempt,
)
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.auth.step_up import rotate_authentication_strength
from vibecanvas_api.config import config
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models import (
    UserWebAuthnChallenge,
    UserWebAuthnCredential,
)


router = APIRouter(prefix="/api/v1/auth/mfa/webauthn", tags=["auth"])
_CHALLENGE_TTL = timedelta(minutes=5)
_STEP_UP_TTL = timedelta(minutes=10)
_CEREMONY_TIMEOUT_MS = 120_000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RegistrationOptionsIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class CredentialResponseIn(BaseModel):
    credential: dict[str, Any]
    name: str = Field(default="Security key", min_length=1, max_length=80)


class AuthenticationResponseIn(BaseModel):
    credential: dict[str, Any]


class DeleteCredentialIn(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


async def _home_identity(session, ctx: AuthContext):
    repo = AuthRepo(session)
    user = await repo.get_user(uuid.UUID(ctx.user_id))
    if user is None:
        raise HTTPException(401, "missing or invalid session")
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(user.tenant_id)},
    )
    return repo, user


async def _password_is_valid(repo: AuthRepo, ctx: AuthContext, password: str) -> bool:
    identity = await repo.find_identity("password", ctx.email)
    return bool(
        identity is not None
        and str(identity.user_id) == ctx.user_id
        and identity.secret
        and verify_password(password, identity.secret)
    )


async def _begin_attempt(ctx: AuthContext):
    try:
        return await begin_login_attempt(
            f"webauthn:{ctx.user_id}:{ctx.session_id}"
        )
    except LoginRateLimitExceeded:
        raise HTTPException(429, "webauthn_attempts_exceeded") from None
    except LoginRateLimitUnavailable:
        raise HTTPException(
            503, "webauthn_security_service_unavailable"
        ) from None


def _descriptor(row: UserWebAuthnCredential) -> PublicKeyCredentialDescriptor:
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


async def _replace_challenge(
    session,
    *,
    ctx: AuthContext,
    tenant_id: uuid.UUID,
    purpose: str,
    challenge: bytes,
) -> None:
    await session.execute(
        delete(UserWebAuthnChallenge).where(
            UserWebAuthnChallenge.session_id == uuid.UUID(ctx.session_id),
            UserWebAuthnChallenge.purpose == purpose,
        )
    )
    session.add(
        UserWebAuthnChallenge(
            user_id=uuid.UUID(ctx.user_id),
            tenant_id=tenant_id,
            session_id=uuid.UUID(ctx.session_id),
            purpose=purpose,
            challenge=challenge,
            expires_at=_now() + _CHALLENGE_TTL,
        )
    )


async def _consume_challenge(
    *,
    ctx: AuthContext,
    purpose: str,
) -> bytes:
    async with session_scope() as session:
        await _home_identity(session, ctx)
        row = (
            await session.execute(
                select(UserWebAuthnChallenge)
                .where(
                    UserWebAuthnChallenge.user_id == uuid.UUID(ctx.user_id),
                    UserWebAuthnChallenge.session_id
                    == uuid.UUID(ctx.session_id),
                    UserWebAuthnChallenge.purpose == purpose,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.expires_at <= _now():
            if row is not None:
                await session.delete(row)
            raise HTTPException(400, "webauthn_challenge_invalid_or_expired")
        challenge = bytes(row.challenge)
        await session.delete(row)
    return challenge


def _credential_id(body: dict[str, Any]) -> str:
    value = body.get("id")
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise HTTPException(400, "webauthn_credential_invalid")
    try:
        return bytes_to_base64url(base64url_to_bytes(value))
    except Exception:
        raise HTTPException(400, "webauthn_credential_invalid") from None


@router.get("")
async def webauthn_status(
    ctx: AuthContext = Depends(current_user),
) -> dict:
    async with session_scope() as session:
        await _home_identity(session, ctx)
        rows = list(
            (
                await session.execute(
                    select(UserWebAuthnCredential)
                    .where(
                        UserWebAuthnCredential.user_id
                        == uuid.UUID(ctx.user_id)
                    )
                    .order_by(UserWebAuthnCredential.created_at)
                )
            ).scalars()
        )
    return {
        "enabled": bool(rows),
        "credentials": [
            {
                "credential_id": row.credential_id,
                "name": row.name,
                "device_type": row.device_type,
                "backed_up": row.backed_up,
                "transports": row.transports,
                "created_at": row.created_at,
                "last_used_at": row.last_used_at,
            }
            for row in rows
        ],
        "authentication_strength": ctx.authentication_strength,
        "step_up_expires_at": ctx.step_up_expires_at,
    }


@router.post("/registration/options")
async def registration_options(
    body: RegistrationOptionsIn,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    attempt = await _begin_attempt(ctx)
    async with session_scope() as session:
        repo, user = await _home_identity(session, ctx)
        if not await _password_is_valid(repo, ctx, body.password):
            await attempt.failure()
            raise HTTPException(403, "password_confirmation_failed")
        rows = list(
            (
                await session.execute(
                    select(UserWebAuthnCredential).where(
                        UserWebAuthnCredential.user_id
                        == uuid.UUID(ctx.user_id)
                    )
                )
            ).scalars()
        )
        options = generate_registration_options(
            rp_id=config.webauthn_rp_id,
            rp_name=config.webauthn_rp_name,
            user_id=uuid.UUID(ctx.user_id).bytes,
            user_name=ctx.email,
            user_display_name=ctx.display_name or ctx.email,
            timeout=_CEREMONY_TIMEOUT_MS,
            exclude_credentials=[_descriptor(row) for row in rows],
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        await _replace_challenge(
            session,
            ctx=ctx,
            tenant_id=user.tenant_id,
            purpose="registration",
            challenge=options.challenge,
        )
    await attempt.success()
    response.headers["Cache-Control"] = "no-store"
    return json.loads(options_to_json(options))


@router.post("/registration/verify", status_code=201)
async def verify_registration(
    body: CredentialResponseIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    attempt = await _begin_attempt(ctx)
    challenge = await _consume_challenge(ctx=ctx, purpose="registration")
    try:
        verified = verify_registration_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=config.webauthn_rp_id,
            expected_origin=config.webauthn_origin,
            require_user_verification=True,
        )
    except WebAuthnException:
        await attempt.failure()
        raise HTTPException(400, "webauthn_registration_invalid") from None
    credential_id = bytes_to_base64url(verified.credential_id)
    transports_raw = body.credential.get("response", {}).get("transports", [])
    valid_transports = {item.value for item in AuthenticatorTransport}
    transports = [
        value
        for value in transports_raw
        if isinstance(value, str) and value in valid_transports
    ]
    elevation = None
    try:
        async with session_scope() as session:
            _repo, user = await _home_identity(session, ctx)
            session.add(
                UserWebAuthnCredential(
                    credential_id=credential_id,
                    user_id=uuid.UUID(ctx.user_id),
                    tenant_id=user.tenant_id,
                    public_key=verified.credential_public_key,
                    sign_count=verified.sign_count,
                    transports=transports,
                    device_type=verified.credential_device_type.value,
                    backed_up=verified.credential_backed_up,
                    name=body.name.strip(),
                )
            )
            elevation = await rotate_authentication_strength(
                session,
                session_id=uuid.UUID(ctx.session_id),
                user_id=uuid.UUID(ctx.user_id),
                strength="webauthn",
                step_up_ttl=_STEP_UP_TTL,
            )
    except IntegrityError:
        await attempt.failure()
        raise HTTPException(409, "webauthn_credential_already_registered") from None
    await attempt.success()
    assert elevation is not None
    session_payload = elevation.publish(response)
    await record_auth_audit(
        action=actions.AUTH_MFA_ENROLL,
        actor_user_id=uuid.UUID(ctx.user_id),
        actor_email=ctx.email,
        tenant_id=uuid.UUID(ctx.tenant_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={"method": "webauthn"},
    )
    return {
        "credential_id": credential_id,
        "authentication_strength": "webauthn",
        "step_up_expires_at": elevation.step_up_expires_at,
        **session_payload,
    }


@router.post("/authentication/options")
async def authentication_options(
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    async with session_scope() as session:
        _repo, user = await _home_identity(session, ctx)
        rows = list(
            (
                await session.execute(
                    select(UserWebAuthnCredential).where(
                        UserWebAuthnCredential.user_id
                        == uuid.UUID(ctx.user_id)
                    )
                )
            ).scalars()
        )
        if not rows:
            raise HTTPException(409, "webauthn_not_enrolled")
        options = generate_authentication_options(
            rp_id=config.webauthn_rp_id,
            timeout=_CEREMONY_TIMEOUT_MS,
            allow_credentials=[_descriptor(row) for row in rows],
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        await _replace_challenge(
            session,
            ctx=ctx,
            tenant_id=user.tenant_id,
            purpose="authentication",
            challenge=options.challenge,
        )
    response.headers["Cache-Control"] = "no-store"
    return json.loads(options_to_json(options))


@router.post("/authentication/verify")
async def verify_authentication(
    body: AuthenticationResponseIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    attempt = await _begin_attempt(ctx)
    credential_id = _credential_id(body.credential)
    challenge = await _consume_challenge(ctx=ctx, purpose="authentication")
    async with session_scope() as session:
        await _home_identity(session, ctx)
        snapshot = await session.get(UserWebAuthnCredential, credential_id)
        if snapshot is None or str(snapshot.user_id) != ctx.user_id:
            await attempt.failure()
            raise HTTPException(400, "webauthn_authentication_invalid")
        public_key = bytes(snapshot.public_key)
        sign_count = int(snapshot.sign_count)
    try:
        verified = verify_authentication_response(
            credential=body.credential,
            expected_challenge=challenge,
            expected_rp_id=config.webauthn_rp_id,
            expected_origin=config.webauthn_origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
            require_user_verification=True,
        )
    except WebAuthnException:
        await attempt.failure()
        await record_auth_audit(
            action=actions.AUTH_MFA_CHALLENGE,
            actor_user_id=uuid.UUID(ctx.user_id),
            actor_email=ctx.email,
            tenant_id=uuid.UUID(ctx.tenant_id),
            outcome="failure",
            audit_ctx=extract_request_audit_context(request),
            meta={"method": "webauthn"},
        )
        raise HTTPException(400, "webauthn_authentication_invalid") from None
    elevation = None
    async with session_scope() as session:
        await _home_identity(session, ctx)
        row = (
            await session.execute(
                select(UserWebAuthnCredential)
                .where(
                    UserWebAuthnCredential.credential_id == credential_id,
                    UserWebAuthnCredential.user_id == uuid.UUID(ctx.user_id),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or int(row.sign_count) != sign_count:
            await attempt.failure()
            raise HTTPException(409, "webauthn_credential_concurrently_used")
        row.sign_count = verified.new_sign_count
        row.device_type = verified.credential_device_type.value
        row.backed_up = verified.credential_backed_up
        row.last_used_at = _now()
        elevation = await rotate_authentication_strength(
            session,
            session_id=uuid.UUID(ctx.session_id),
            user_id=uuid.UUID(ctx.user_id),
            strength="webauthn",
            step_up_ttl=_STEP_UP_TTL,
        )
    await attempt.success()
    assert elevation is not None
    session_payload = elevation.publish(response)
    await record_auth_audit(
        action=actions.AUTH_MFA_CHALLENGE,
        actor_user_id=uuid.UUID(ctx.user_id),
        actor_email=ctx.email,
        tenant_id=uuid.UUID(ctx.tenant_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={"method": "webauthn"},
    )
    return {
        "authentication_strength": "webauthn",
        "step_up_expires_at": elevation.step_up_expires_at,
        **session_payload,
    }


@router.delete("/credentials/{credential_id}", status_code=204)
async def delete_credential(
    credential_id: str,
    body: DeleteCredentialIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(require_recent_step_up),
) -> None:
    canonical_id = _credential_id({"id": credential_id})
    elevation = None
    async with session_scope() as session:
        repo, _user = await _home_identity(session, ctx)
        if not await _password_is_valid(repo, ctx, body.password):
            raise HTTPException(403, "password_confirmation_failed")
        row = await session.get(UserWebAuthnCredential, canonical_id)
        if row is None or str(row.user_id) != ctx.user_id:
            raise HTTPException(404, "webauthn_credential_not_found")
        await session.delete(row)
        elevation = await rotate_authentication_strength(
            session,
            session_id=uuid.UUID(ctx.session_id),
            user_id=uuid.UUID(ctx.user_id),
            strength="password",
        )
    assert elevation is not None
    elevation.publish(response)
    await record_auth_audit(
        action=actions.AUTH_MFA_DISABLE,
        actor_user_id=uuid.UUID(ctx.user_id),
        actor_email=ctx.email,
        tenant_id=uuid.UUID(ctx.tenant_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={"method": "webauthn"},
    )
