"""Account TOTP enrollment, replay-safe step-up, and recovery."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text

from vibecanvas_api.audit import actions
from vibecanvas_api.audit.context import extract_request_audit_context
from vibecanvas_api.audit.service import record_auth_audit
from vibecanvas_api.auth.deps import AuthContext, current_user
from vibecanvas_api.auth.mfa import (
    matching_totp_step,
    new_recovery_codes,
    new_totp_secret,
    provisioning_uri,
    recovery_code_hash,
)
from vibecanvas_api.auth.mfa_storage import decrypt_totp_secret
from vibecanvas_api.auth.password import verify_password
from vibecanvas_api.auth.ratelimit import (
    LoginRateLimitExceeded,
    LoginRateLimitUnavailable,
    begin_login_attempt,
)
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.auth.step_up import (
    SessionElevation,
    rotate_authentication_strength,
)
from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models import Session, UserMfaTotp


router = APIRouter(prefix="/api/v1/auth/mfa", tags=["auth"])
_ENROLLMENT_TTL = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PasswordConfirmation(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


class MfaCodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class DisableMfaIn(MfaCodeIn, PasswordConfirmation):
    pass


async def _home_identity(
    session,
    ctx: AuthContext,
) -> tuple[AuthRepo, object]:
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


async def _rotate_factor_session(
    session,
    *,
    ctx: AuthContext,
    strength: str,
) -> SessionElevation:
    try:
        return await rotate_authentication_strength(
            session,
            session_id=uuid.UUID(ctx.session_id),
            user_id=uuid.UUID(ctx.user_id),
            strength=strength,
        )
    except LookupError:
        raise HTTPException(401, "missing or invalid session") from None


async def _begin_attempt(ctx: AuthContext):
    try:
        return await begin_login_attempt(f"mfa:{ctx.user_id}:{ctx.session_id}")
    except LoginRateLimitExceeded:
        raise HTTPException(429, "mfa_attempts_exceeded") from None
    except LoginRateLimitUnavailable:
        raise HTTPException(503, "mfa_security_service_unavailable") from None


@router.get("")
async def mfa_status(ctx: AuthContext = Depends(current_user)) -> dict:
    async with session_scope() as session:
        await _home_identity(session, ctx)
        row = await session.get(UserMfaTotp, uuid.UUID(ctx.user_id))
        return {
            "enabled": bool(row is not None and row.status == "active"),
            "pending": bool(
                row is not None
                and row.status == "pending"
                and row.pending_expires_at is not None
                and row.pending_expires_at > _now()
            ),
            "authentication_strength": ctx.authentication_strength,
            "step_up_expires_at": (
                ctx.step_up_expires_at.isoformat()
                if ctx.step_up_expires_at is not None
                else None
            ),
        }


@router.post("/totp/enroll", status_code=201)
async def enroll_totp(
    body: PasswordConfirmation,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    attempt = await _begin_attempt(ctx)
    async with session_scope() as session:
        repo, user = await _home_identity(session, ctx)
        if not await _password_is_valid(repo, ctx, body.password):
            await attempt.failure()
            raise HTTPException(403, "password_confirmation_failed")
        existing = await session.get(UserMfaTotp, uuid.UUID(ctx.user_id))
        if existing is not None and existing.status == "active":
            raise HTTPException(409, "mfa_already_enabled")
        secret = new_totp_secret()
        encrypted = await content_encryption_service().encrypt_json(
            session,
            tenant_id=user.tenant_id,
            resource_type="user_identity",
            resource_id=ctx.user_id,
            purpose="mfa_totp_seed",
            record_id=ctx.user_id,
            value={"secret": secret},
        )
        row = existing or UserMfaTotp(
            user_id=uuid.UUID(ctx.user_id),
            tenant_id=user.tenant_id,
            secret_ciphertext=encrypted.ciphertext,
            secret_nonce=encrypted.nonce,
            secret_key_id=encrypted.key_id,
        )
        row.status = "pending"
        row.tenant_id = user.tenant_id
        row.secret_ciphertext = encrypted.ciphertext
        row.secret_nonce = encrypted.nonce
        row.secret_key_id = encrypted.key_id
        row.last_used_step = None
        row.recovery_code_hashes = []
        row.pending_expires_at = _now() + _ENROLLMENT_TTL
        row.updated_at = _now()
        session.add(row)
    await attempt.success()
    response.headers["Cache-Control"] = "no-store"
    await record_auth_audit(
        action=actions.AUTH_MFA_ENROLL,
        actor_user_id=uuid.UUID(ctx.user_id),
        actor_email=ctx.email,
        tenant_id=uuid.UUID(ctx.tenant_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={"phase": "pending"},
    )
    return {
        "secret": secret,
        "provisioning_uri": provisioning_uri(
            secret=secret,
            account_name=ctx.email,
            issuer="Skeinix",
        ),
        "expires_in": int(_ENROLLMENT_TTL.total_seconds()),
    }


@router.post("/totp/confirm")
async def confirm_totp(
    body: MfaCodeIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    attempt = await _begin_attempt(ctx)
    recovery_codes: list[str] = []
    elevation: SessionElevation | None = None
    valid = False
    async with session_scope() as session:
        await _home_identity(session, ctx)
        row = (
            await session.execute(
                select(UserMfaTotp)
                .where(UserMfaTotp.user_id == uuid.UUID(ctx.user_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if (
            row is not None
            and row.status == "pending"
            and row.pending_expires_at is not None
            and row.pending_expires_at > _now()
        ):
            secret = await decrypt_totp_secret(session, row)
            step = matching_totp_step(secret, body.code)
            if step is not None:
                recovery_codes = new_recovery_codes()
                row.status = "active"
                row.last_used_step = step
                row.recovery_code_hashes = [
                    recovery_code_hash(user_id=ctx.user_id, code=code)
                    for code in recovery_codes
                ]
                row.pending_expires_at = None
                row.updated_at = _now()
                elevation = await _rotate_factor_session(
                    session,
                    ctx=ctx,
                    strength="totp",
                )
                valid = True
    if not valid:
        await attempt.failure()
        raise HTTPException(400, "mfa_code_invalid_or_expired")
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
        meta={"phase": "active"},
    )
    return {
        "enabled": True,
        "recovery_codes": recovery_codes,
        "authentication_strength": "totp",
        "step_up_expires_at": None,
        **session_payload,
    }


@router.post("/challenge")
async def challenge_mfa(
    body: MfaCodeIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> dict:
    attempt = await _begin_attempt(ctx)
    valid = False
    method = "totp"
    elevation: SessionElevation | None = None
    async with session_scope() as session:
        await _home_identity(session, ctx)
        row = (
            await session.execute(
                select(UserMfaTotp)
                .where(
                    UserMfaTotp.user_id == uuid.UUID(ctx.user_id),
                    UserMfaTotp.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None:
            secret = await decrypt_totp_secret(session, row)
            step = matching_totp_step(secret, body.code)
            if step is not None and (
                row.last_used_step is None or step > row.last_used_step
            ):
                row.last_used_step = step
                valid = True
            else:
                candidate = recovery_code_hash(
                    user_id=ctx.user_id,
                    code=body.code,
                )
                if candidate in row.recovery_code_hashes:
                    row.recovery_code_hashes = [
                        value for value in row.recovery_code_hashes
                        if value != candidate
                    ]
                    valid = True
                    method = "recovery"
            if valid:
                row.updated_at = _now()
                elevation = await _rotate_factor_session(
                    session,
                    ctx=ctx,
                    strength="totp" if method == "totp" else "recovery",
                )
    if not valid:
        await attempt.failure()
        await record_auth_audit(
            action=actions.AUTH_MFA_CHALLENGE,
            actor_user_id=uuid.UUID(ctx.user_id),
            actor_email=ctx.email,
            tenant_id=uuid.UUID(ctx.tenant_id),
            outcome="failure",
            audit_ctx=extract_request_audit_context(request),
            meta={},
        )
        raise HTTPException(400, "mfa_code_invalid_or_replayed")
    await attempt.success()
    assert elevation is not None
    session_payload = elevation.publish(response)
    await record_auth_audit(
        action=(
            actions.AUTH_MFA_RECOVERY
            if method == "recovery"
            else actions.AUTH_MFA_CHALLENGE
        ),
        actor_user_id=uuid.UUID(ctx.user_id),
        actor_email=ctx.email,
        tenant_id=uuid.UUID(ctx.tenant_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={},
    )
    return {
        "authentication_strength": "totp" if method == "totp" else "recovery",
        "step_up_expires_at": None,
        **session_payload,
    }


@router.delete("", status_code=204)
async def disable_mfa(
    body: DisableMfaIn,
    request: Request,
    response: Response,
    ctx: AuthContext = Depends(current_user),
) -> None:
    attempt = await _begin_attempt(ctx)
    valid = False
    elevation: SessionElevation | None = None
    async with session_scope() as session:
        repo, _user = await _home_identity(session, ctx)
        if not await _password_is_valid(repo, ctx, body.password):
            await attempt.failure()
            raise HTTPException(403, "password_confirmation_failed")
        row = (
            await session.execute(
                select(UserMfaTotp)
                .where(
                    UserMfaTotp.user_id == uuid.UUID(ctx.user_id),
                    UserMfaTotp.status == "active",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is not None:
            secret = await decrypt_totp_secret(session, row)
            step = matching_totp_step(secret, body.code)
            if step is not None and (
                row.last_used_step is None or step > row.last_used_step
            ):
                valid = True
            else:
                candidate = recovery_code_hash(user_id=ctx.user_id, code=body.code)
                valid = candidate in row.recovery_code_hashes
        if valid:
            await session.delete(row)
            await session.execute(
                delete(Session).where(
                    Session.user_id == uuid.UUID(ctx.user_id),
                    Session.session_id != uuid.UUID(ctx.session_id),
                )
            )
            elevation = await _rotate_factor_session(
                session,
                ctx=ctx,
                strength="password",
            )
    if not valid:
        await attempt.failure()
        raise HTTPException(400, "mfa_code_invalid_or_replayed")
    await attempt.success()
    assert elevation is not None
    elevation.publish(response)
    await record_auth_audit(
        action=actions.AUTH_MFA_DISABLE,
        actor_user_id=uuid.UUID(ctx.user_id),
        actor_email=ctx.email,
        tenant_id=uuid.UUID(ctx.tenant_id),
        outcome="success",
        audit_ctx=extract_request_audit_context(request),
        meta={},
    )
