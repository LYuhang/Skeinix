"""Request-path logs carry the resolved tenant identifier.

The request-id middleware binds tenant_id=None at request start (auth hasn't
run yet). Once auth resolves the session, `resolve_authenticated_user` calls
`context.bind_tenant_id(...)` so every subsequent log line in the same
per-request ASGI contextvar context carries the real tenant_id.
"""
import json
import types
from unittest.mock import AsyncMock

import pytest
import structlog

from vibecanvas_api.observability import context
from vibecanvas_api.observability.logging import configure_logging


def test_bind_tenant_id_sets_contextvar_and_log_line(capsys):
    """Test A — directly exercise the context helper and confirm a structlog
    line emitted afterward carries the bound tenant_id."""
    token = context.bind_request_context(request_id="req-A", tenant_id=None)
    try:
        # Baseline: no tenant bound yet by auth.
        assert context.get_tenant_id() is None

        context.bind_tenant_id("ten-X")
        assert context.get_tenant_id() == "ten-X"

        configure_logging(force_format="json")
        structlog.get_logger("test").info("authed-request")
    finally:
        context.reset_request_context(token)

    # reset restores the baseline (None) at request end.
    assert context.get_tenant_id() is None

    out = capsys.readouterr().out.strip().splitlines()[-1]
    rec = json.loads(out)
    assert rec["event"] == "authed-request"
    assert rec["request_id"] == "req-A"
    assert rec["tenant_id"] == "ten-X"


@pytest.mark.asyncio
async def test_resolve_authenticated_user_binds_tenant(monkeypatch):
    """Test B — `resolve_authenticated_user` binds the resolved tenant_id via
    obs_context. We stub AuthRepo + the bind helper (spy) so no DB is needed."""
    from vibecanvas_api.auth import deps

    fake_session_row = types.SimpleNamespace(
        user_id="user-99",
        tenant_id="ten-Z",
        active_organization_id="ten-Z",
        token_hash="hash-1",
        generation=3,
        authentication_strength="password",
        step_up_expires_at=None,
        session_id="session-1",
        audience="web",
    )
    fake_user = types.SimpleNamespace(
        email="a@example.com", status="active", display_name="A",
    )
    fake_membership = types.SimpleNamespace(
        membership_id="membership-1",
        org_role="owner",
        status="active",
    )

    fake_repo = types.SimpleNamespace(
        resolve_session=AsyncMock(return_value=fake_session_row),
        touch_session=AsyncMock(return_value=None),
        get_user=AsyncMock(return_value=fake_user),
        get_membership=AsyncMock(return_value=fake_membership),
    )
    monkeypatch.setattr(deps, "AuthRepo", lambda session: fake_repo)
    monkeypatch.setattr(deps, "hash_token", lambda raw: "hash-1")

    bound: list[str | None] = []
    monkeypatch.setattr(deps.obs_context, "bind_tenant_id", bound.append)

    ctx = await deps.resolve_authenticated_user("raw-token", session=object())

    assert ctx.tenant_id == "ten-Z"
    assert ctx.active_organization_id == "ten-Z"
    assert ctx.membership_role == "owner"
    assert bound == ["ten-Z"]
