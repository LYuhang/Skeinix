"""T4 — auth routes emit audit rows; no secrets; unknown-email → NULL tenant.

Driven through the REAL HTTP auth client (the conftest ``client`` fixture
against the ASGI app) so the hooks are exercised end-to-end. ``record_auth_audit``
uses the admin engine (``db._admin_engine`` via ``session_scope_admin``); we
inject the superuser ``pg_engine`` there so the explicit NULL-tenant raw INSERT
is permitted (FORCE RLS would otherwise block it as ``vibecanvas_app``).

Audit rows are read back via the superuser ``pg_engine`` (RLS-bypass) — a
NULL-tenant auth row is invisible to any tenant-scoped session. ``audit_log`` is
NOT in conftest's truncate list, so we use unique marker emails to scope the
read-backs robustly even if sibling-test rows coexist.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text


@pytest_asyncio.fixture
async def admin_engine_injected(monkeypatch, pg_engine):
    """Inject the superuser ``pg_engine`` as ``db._admin_engine`` so
    ``session_scope_admin`` (the engine ``record_auth_audit`` writes through)
    runs RLS-bypassing — otherwise the explicit NULL-tenant INSERT fails FORCE
    RLS as ``vibecanvas_app``. Mirrors ``test_audit_service.py`` / batch tests."""
    from vibecanvas_api.storage import db as db_mod
    monkeypatch.setattr(db_mod, "_admin_engine", pg_engine)
    yield pg_engine


async def _rows_for_email(pg_engine, email):
    from vibecanvas_api.security.audit_protection import audit_lookup_digest

    async with pg_engine.connect() as c:
        res = await c.execute(text(
            "SELECT tenant_id, actor_user_id, actor_email, actor_lookup_hash, "
            "action, outcome, meta FROM audit_log "
            "WHERE actor_lookup_hash = :digest ORDER BY created_at"
        ), {"digest": audit_lookup_digest("actor_email", email)})
        return list(res.mappings())


@pytest.mark.asyncio
async def test_login_failure_unknown_email_writes_null_tenant_row(
        client, admin_engine_injected):
    """POST /login with a non-existent email → 401 AND an auth.login_failure
    row with tenant_id NULL, actor_user_id NULL, actor_email = the attempt."""
    pg_engine = admin_engine_injected
    email = f"ghost-{uuid.uuid4().hex[:8]}@nope.com"
    r = await client.post("/api/v1/auth/login",
                          json={"email": email, "password": "whatever12345"})
    assert r.status_code == 401

    rows = await _rows_for_email(pg_engine, email)
    assert len(rows) == 1
    row = rows[0]
    assert row["action"] == "auth.login_failure"
    assert row["outcome"] == "failure"
    assert row["tenant_id"] is None          # unknown email → no tenant
    assert row["actor_user_id"] is None


@pytest.mark.asyncio
async def test_login_success_writes_row_with_tenant(client, admin_engine_injected):
    """Register a user, login → auth.login_success row carrying that
    tenant + user (resolved from the committed session)."""
    pg_engine = admin_engine_injected
    email = f"ok-{uuid.uuid4().hex[:8]}@example.com"
    reg = await client.post("/api/v1/auth/register",
                            json={"email": email, "username": "Test User", "password": "pw12345678"})
    assert reg.status_code == 201
    user_id = reg.json()["user"]["user_id"]

    lr = await client.post("/api/v1/auth/login",
                           json={"email": email, "password": "pw12345678"})
    assert lr.status_code == 200

    rows = await _rows_for_email(pg_engine, email)
    actions_seen = {r["action"] for r in rows}
    assert "auth.register" in actions_seen
    assert "auth.login_success" in actions_seen

    success = [r for r in rows if r["action"] == "auth.login_success"]
    assert len(success) == 1
    s = success[0]
    assert s["outcome"] == "success"
    assert s["tenant_id"] is not None
    assert str(s["actor_user_id"]) == user_id


@pytest.mark.asyncio
async def test_no_password_in_any_audit_row(client, admin_engine_injected):
    """After a login success AND a wrong-password failure for a known user,
    scan every audit_log column + meta: the plaintext password must NOT
    appear anywhere."""
    pg_engine = admin_engine_injected
    email = f"sec-{uuid.uuid4().hex[:8]}@example.com"
    secret = f"SuPerSecret-{uuid.uuid4().hex}"
    reg = await client.post("/api/v1/auth/register",
                            json={"email": email, "username": "Test User", "password": secret})
    assert reg.status_code == 201

    ok = await client.post("/api/v1/auth/login",
                           json={"email": email, "password": secret})
    assert ok.status_code == 200
    bad = await client.post("/api/v1/auth/login",
                            json={"email": email, "password": secret + "WRONG"})
    assert bad.status_code == 401

    # Serialize every column of every row for this email and assert the secret
    # (and its wrong variant) appear nowhere.
    async with pg_engine.connect() as c:
        from vibecanvas_api.security.audit_protection import audit_lookup_digest

        res = await c.execute(text(
            "SELECT tenant_id::text, actor_user_id::text, actor_email, action, "
            "       target_type, target_id, target_name, outcome, ip_address, "
            "       user_agent, request_id, meta::text, private_ciphertext "
            "FROM audit_log WHERE actor_lookup_hash = :digest"
        ), {"digest": audit_lookup_digest("actor_email", email)})
        blob = "\n".join(
            "|".join("" if v is None else str(v) for v in row)
            for row in res
        )
    assert secret not in blob
    assert (secret + "WRONG") not in blob
