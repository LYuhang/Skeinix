import pytest
from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.auth.password import hash_password
from vibecanvas_api.auth.tokens import new_token
from vibecanvas_api.auth.deps import (
    AuthContext,
    require_recent_step_up,
    resolve_authenticated_user,
)
from vibecanvas_api.config import config


@pytest.mark.asyncio
async def test_resolve_valid_session(pg_session):
    repo = AuthRepo(pg_session)
    user = await repo.register("e@example.com", hash_password("pw12345678"))
    raw, hashed = new_token()
    await repo.create_session(hashed, user.user_id, user.tenant_id,
                              datetime.now(timezone.utc) + timedelta(days=30))
    ctx = await resolve_authenticated_user(raw, pg_session)
    assert str(ctx.user_id) == str(user.user_id)
    assert str(ctx.tenant_id) == str(user.tenant_id)
    assert ctx.email == "e@example.com"


@pytest.mark.asyncio
async def test_resolve_bad_token_401(pg_session):
    with pytest.raises(HTTPException) as ei:
        await resolve_authenticated_user("not-a-real-token", pg_session)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_high_risk_step_up_requires_fresh_webauthn(monkeypatch):
    monkeypatch.setattr(config, "high_risk_step_up_required", True)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)

    password = AuthContext(
        user_id="user",
        tenant_id="tenant",
        email="person@example.com",
        authentication_strength="password",
        step_up_expires_at=future,
        session_id="session",
    )
    with pytest.raises(HTTPException) as exc:
        await require_recent_step_up(password)
    assert exc.value.status_code == 403
    assert exc.value.detail == {
        "code": "step_up_required",
        "method": "webauthn",
    }

    webauthn = AuthContext(
        user_id="user",
        tenant_id="tenant",
        email="person@example.com",
        authentication_strength="webauthn",
        step_up_expires_at=future,
        session_id="session",
    )
    assert await require_recent_step_up(webauthn) is webauthn

    expired = AuthContext(
        user_id="user",
        tenant_id="tenant",
        email="person@example.com",
        authentication_strength="webauthn",
        step_up_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        session_id="session",
    )
    with pytest.raises(HTTPException):
        await require_recent_step_up(expired)
