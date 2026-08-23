"""T5 — resource routes emit one audit row, atomic, no secrets.

Strategy: handler-direct-call + ``_StubCtx`` + ``_StubRequest`` (mirrors
``test_deployments_crud.py`` / ``test_mcp_servers_crud.py`` — the TestClient
ride over the real ``current_user`` Bearer DI is fragile in this repo;
calling the handler directly exercises the exact repo + soft-delete +
secret paths AND lets us assert the audit row lands on the SAME tenant_db
session, committing atomically with the action).

The audit row is ORM-added to the route's tenant session; we ``await
s.commit()`` (the FastAPI tenant_db teardown does this in prod) and then
read the row back. ``audit_log`` is NOT in conftest's truncate list, so the
read-backs are scoped by the freshly-minted ``tenant_id`` of each test.

Covered:
* deployment delete → exactly one ``deployment.delete`` row, with
  ``target_name`` captured before the soft-delete (G7 + atomic).
* deployment rotate-key → ``deployment.key_rotate`` row that does NOT
  contain the new plaintext ``vc_`` key in any column (no-secrets / G5).
* mcp PATCH → ``mcp_server.credential_change`` fires ONLY when
  ``auth_config`` is patched, and the seeded bearer token never lands in
  any audit column (conditional + no-secrets).
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.security.secret_service import secret_service
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_mcp_servers import McpServersRepo
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


# --------------------------------------------------------------------- stubs


class _StubCtx:
    """Stand-in for ``AuthContext`` — the hooks read ``user_id`` / ``email``
    for the actor columns; ``tenant_id`` matches the session GUC."""

    def __init__(self, tenant_id, user_id, email="actor@example.com"):
        self.tenant_id = str(tenant_id)
        self.user_id = str(user_id)
        self.active_organization_id = str(tenant_id)
        self.session_id = "test-session"
        self.session_generation = 1
        self.membership_id = "test-membership"
        self.membership_role = "owner"
        self.membership_status = "active"
        self.authentication_strength = "password"
        self.email = email


class _StubRequest:
    """Minimal stand-in for ``starlette.Request`` for the audit context
    extractor: ``.headers`` (a dict-like) + ``.client.host``."""

    def __init__(self, ip="1.2.3.4", ua="pytest/1"):
        self.headers = {"X-Forwarded-For": ip, "User-Agent": ua}
        self.client = type("C", (), {"host": ip})()
        self.state = SimpleNamespace(request_id="audit-test")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")


# --------------------------------------------------------------------- seeds


async def _seed_tenant_user(pg_engine):
    """Insert a fresh tenant + user via the superuser engine (auth tables
    are RLS-free). Returns ``(tenant_id, user_id)``."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tenant_id},
        )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": user_id, "t": tenant_id,
             "e": f"t5-{uuid.uuid4().hex[:6]}@example.com"},
        )
        await c.execute(
            text(
                "INSERT INTO organizations(tenant_id, kind, slug, name, created_by) "
                "VALUES (:t, 'personal', :slug, 'Audit account', :u)"
            ),
            {"t": tenant_id, "u": user_id, "slug": f"audit-{tenant_id.hex}"},
        )
    return tenant_id, user_id


async def _seed_wf(app_engine, tenant_id, user_id):
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    async with session_scope(tenant_id=str(tenant_id)) as session:
        await WorkflowRepo(session, str(user_id)).create_workflow(
            wf_id=wf_id,
            name="Audit Workflow",
        )
    return wf_id


async def _seed_api_dep(app_engine, tenant_id, user_id, wf_id, *, name="DepName"):
    """Seed an api-type deployment row (has an api_key_hash)."""
    import hashlib
    dep_id = uuid.uuid4()
    h = hashlib.sha256(f"key-{uuid.uuid4().hex}".encode()).hexdigest()
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant_id)},
        )
        await c.execute(
            text(
                "INSERT INTO deployments("
                "id, tenant_id, user_id, owner_id, wf_id, name, slug, "
                "trigger_type, version_pin, pinned_major, pinned_sub, "
                "api_key_hash"
                ") VALUES ("
                ":id, :t, :u, :u, :w, :n, :s, "
                "'api', 'specific', 1, 0, :h"
                ")"
            ),
            {"id": dep_id, "t": tenant_id, "u": user_id, "w": wf_id,
             "n": name, "s": f"dep-{uuid.uuid4().hex[:6]}", "h": h},
        )
        await c.commit()
    return dep_id


async def _seed_mcp_server(pg_engine, tenant_id, user_id, *, name="McpName",
                           tool_prefix="mcpx", token="tok-SEEDED-SECRET"):
    """Seed an MCP row through the strict encrypted credential path."""
    sid = uuid.uuid4()
    async with session_scope(tenant_id=str(tenant_id)) as session:
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=tenant_id,
            purpose="mcp_bearer_token",
            resource_type="mcp_installation",
            resource_id=sid,
            plaintext=token,
        )
        await McpServersRepo(session).insert(
            id=sid,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            tool_prefix=tool_prefix,
            transport="sse",
            endpoint="https://events.example.test/sse",
            auth_config={"type": "bearer"},
            auth_secret_ref=secret_ref,
            enabled=True,
            last_handshake_status="ok",
            last_tool_count=0,
            last_tool_names=[],
        )
    return sid


async def _audit_rows(pg_engine, tenant_id):
    """Read application-level audit projections through envelope decryption."""
    from vibecanvas_api.audit.repo import AuditRepo

    async with session_scope(tenant_id=str(tenant_id)) as session:
        rows = await AuditRepo(session).list_for_tenant(limit=1000)
    return [
        {
            "action": row.action,
            "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
            "actor_email": row.actor_email,
            "target_type": row.target_type,
            "target_id": row.target_id,
            "target_name": row.target_name,
            "outcome": row.outcome,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
            "tenant_id": str(row.tenant_id) if row.tenant_id else None,
            "meta": row.meta,
        }
        for row in reversed(rows)
    ]


async def _audit_blob(pg_engine, tenant_id):
    """Serialize every column of every audit row for a tenant — for the
    no-secrets scan."""
    async with pg_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT actor_email, target_name, ip_address, user_agent, "
                    "meta::text, private_ciphertext FROM audit_log "
                    "WHERE tenant_id=:tenant_id"
                ),
                {"tenant_id": tenant_id},
            )
        ).all()
    return "\n".join(
        "|".join("" if value is None else str(value) for value in row)
        for row in rows
    )


# ----------------------------------------------------------- deployment delete


@pytest.mark.asyncio
async def test_deployment_delete_emits_audit_row(pg_engine, app_engine):
    """DELETE → exactly one ``deployment.delete`` row, target_type/id/name set,
    committed atomically on the same tenant_db session."""
    from vibecanvas_api.routes.deployments import delete_deployment
    from vibecanvas_api.storage.db import session_scope

    t, u = await _seed_tenant_user(pg_engine)
    wf = await _seed_wf(app_engine, t, u)
    dep_id = await _seed_api_dep(app_engine, t, u, wf, name="DeleteMe")
    ctx = _StubCtx(t, u, email="del@example.com")

    async with session_scope(tenant_id=str(t)) as s:
        resp = await delete_deployment(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        assert resp.status_code == 204
        await s.commit()

    rows = await _audit_rows(pg_engine, t)
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "deployment.delete"
    assert r["target_type"] == "deployment"
    assert r["target_id"] == str(dep_id)
    assert r["target_name"] == "DeleteMe"      # captured BEFORE soft-delete
    assert r["outcome"] == "success"
    assert r["actor_email"] == "del@example.com"
    assert str(r["actor_user_id"]) == str(u)
    assert r["ip_address"] == "1.2.3.4"        # from the request context


@pytest.mark.asyncio
async def test_deployment_delete_commits_action_with_audit(pg_engine, app_engine):
    """The route-owned commit persists the soft-delete and audit together.

    Structural authorization mutations are enqueued in the same transaction,
    so the endpoint commits before applying the external relationship delta.
    A caller rollback after the handler returns must not undo only one side.
    """
    from vibecanvas_api.routes.deployments import delete_deployment
    from vibecanvas_api.storage.db import session_scope

    t, u = await _seed_tenant_user(pg_engine)
    wf = await _seed_wf(app_engine, t, u)
    dep_id = await _seed_api_dep(app_engine, t, u, wf)
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        await delete_deployment(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.rollback()

    rows = await _audit_rows(pg_engine, t)
    assert [row["action"] for row in rows] == ["deployment.delete"]
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t)},
        )
        deleted_at = (
            await c.execute(
                text("SELECT deleted_at FROM deployments WHERE id = :id"),
                {"id": dep_id},
            )
        ).scalar_one()
    assert deleted_at is not None


# --------------------------------------------------------------- rotate-key


@pytest.mark.asyncio
async def test_key_rotate_emits_row_without_the_new_key(pg_engine, app_engine):
    """rotate-key → one ``deployment.key_rotate`` row whose NEW plaintext
    ``vc_`` key appears in NO audit column (no-secrets)."""
    from vibecanvas_api.routes.deployments import rotate_key
    from vibecanvas_api.storage.db import session_scope

    t, u = await _seed_tenant_user(pg_engine)
    wf = await _seed_wf(app_engine, t, u)
    dep_id = await _seed_api_dep(app_engine, t, u, wf, name="RotateMe")
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        resp = await rotate_key(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.commit()
    new_key = resp["api_key"]
    assert new_key.startswith("vc_")

    rows = await _audit_rows(pg_engine, t)
    assert len(rows) == 1
    assert rows[0]["action"] == "deployment.key_rotate"
    assert rows[0]["target_id"] == str(dep_id)
    assert rows[0]["target_name"] == "RotateMe"

    blob = await _audit_blob(pg_engine, t)
    assert new_key not in blob, "the new plaintext key leaked into an audit row"


# --------------------------------------------------- mcp credential_change


@pytest.mark.asyncio
async def test_mcp_credential_change_fires_when_auth_config_patched(pg_engine):
    """PATCH with ``auth_config`` → exactly one ``mcp_server.credential_change``
    row; the new + old bearer token never lands in any audit column."""
    from vibecanvas_api.routes.mcp_servers import PatchBody, patch_mcp_server
    from vibecanvas_api.storage.db import session_scope

    t, u = await _seed_tenant_user(pg_engine)
    sid = await _seed_mcp_server(pg_engine, t, u, token="OLD-tok-SECRET")
    ctx = _StubCtx(t, u)
    new_token = "NEW-tok-SECRET"

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        new_callable=AsyncMock,
    ) as mock_hs:
        mock_hs.return_value = {
            "status": "ok", "tool_count": 0, "tool_names": [], "tools": [],
        }
        async with session_scope(tenant_id=str(t)) as s:
            body = PatchBody.model_validate(
                {"auth_config": {"type": "bearer", "token": new_token}},
            )
            await patch_mcp_server(
                server_id=sid, body=body, request=_StubRequest(),
                ctx=ctx, session=s, service=_AllowAuthz(),
            )
            await s.commit()

    rows = await _audit_rows(pg_engine, t)
    cred = [r for r in rows if r["action"] == "mcp_server.credential_change"]
    assert len(cred) == 1
    assert cred[0]["target_type"] == "mcp_server"
    assert cred[0]["target_id"] == str(sid)
    assert cred[0]["target_name"] == "McpName"

    blob = await _audit_blob(pg_engine, t)
    assert new_token not in blob
    assert "OLD-tok-SECRET" not in blob


@pytest.mark.asyncio
async def test_mcp_credential_change_not_fired_without_auth_config(pg_engine):
    """PATCH WITHOUT ``auth_config`` (e.g. ``{"enabled": False}``) → NO
    ``mcp_server.credential_change`` row."""
    from vibecanvas_api.routes.mcp_servers import PatchBody, patch_mcp_server
    from vibecanvas_api.storage.db import session_scope

    t, u = await _seed_tenant_user(pg_engine)
    sid = await _seed_mcp_server(pg_engine, t, u)
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        body = PatchBody.model_validate({"enabled": False})
        await patch_mcp_server(
            server_id=sid, body=body, request=_StubRequest(),
            ctx=ctx, session=s, service=_AllowAuthz(),
        )
        await s.commit()

    rows = await _audit_rows(pg_engine, t)
    cred = [r for r in rows if r["action"] == "mcp_server.credential_change"]
    assert cred == [], "credential_change must NOT fire without an auth_config patch"
