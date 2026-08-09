"""T2 — AuditContext extraction + AuditRepo insert/list.

The DB test binds an AsyncSession to the non-superuser ``vibecanvas_app``
``app_engine`` (FORCE RLS applies) and sets ``app.tenant_id`` from the GUC so
``add_row`` (which OMITS tenant_id) lets the migration-009 server-default fill
it. Mirrors the T1 ``test_audit_schema.py`` inline engine+GUC pattern.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from vibecanvas_api.audit.context import AuditContext, extract_request_audit_context
from vibecanvas_api.audit.repo import AuditRepo


# ---------------------------------------------------------------------------
# Context extraction (pure, no DB — SimpleNamespace stands in for Request).
# ---------------------------------------------------------------------------
def test_extract_context_uses_rightmost_untrusted_hop_behind_trusted_proxy():
    req = SimpleNamespace(
        headers={
            "X-Forwarded-For": "6.6.6.6, 1.2.3.4, 10.0.0.2",
            "User-Agent": "UA/1",
        },
        client=SimpleNamespace(host="10.0.0.1"),
    )
    ctx = extract_request_audit_context(
        req,
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    assert ctx.ip_address == "1.2.3.4"
    assert ctx.user_agent == "UA/1"


def test_extract_context_ignores_forwarded_for_from_untrusted_peer():
    req = SimpleNamespace(
        headers={"X-Forwarded-For": "1.2.3.4"},
        client=SimpleNamespace(host="9.9.9.9"),
    )
    ctx = extract_request_audit_context(
        req,
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    assert ctx.ip_address == "9.9.9.9"


def test_extract_context_rejects_malformed_forwarded_chain():
    req = SimpleNamespace(
        headers={"X-Forwarded-For": "not-an-ip, 10.0.0.2"},
        client=SimpleNamespace(host="10.0.0.1"),
    )
    ctx = extract_request_audit_context(
        req,
        trusted_proxy_cidrs=("10.0.0.0/8",),
    )
    assert ctx.ip_address == "10.0.0.1"


def test_extract_context_falls_back_to_client_host():
    req = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"))
    ctx = extract_request_audit_context(req)
    assert ctx.ip_address == "9.9.9.9"
    assert ctx.user_agent is None
    assert isinstance(ctx, AuditContext)


# ---------------------------------------------------------------------------
# Repo insert + tenant-scoped cursor list.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_repo_add_and_list_for_tenant(app_engine):
    """add_row inserts; list_for_tenant returns newest-first, tenant-scoped."""
    tid = uuid.uuid4()
    async with app_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tid},
        )

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as s:
        # Bind the tenant GUC so the RLS server-default fills tenant_id.
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tid)},
        )
        repo = AuditRepo(s)
        # Two separate commits so created_at (transaction-start now()) differs
        # → the (created_at, audit_id) DESC keyset deterministically orders
        # newest-first (a same-transaction tie would fall back to the random
        # audit_id, which is non-deterministic).
        await repo.add_row(
            action="workflow.delete", actor_user_id=None, actor_email="a@e.com",
            target_type="workflow", target_id="wf_1", target_name="First",
            outcome="success", ip_address="1.1.1.1", user_agent="UA",
            request_id="req-1", meta={"k": "v"},
        )
        await s.commit()
        # Re-bind the GUC (a new transaction after commit resets it).
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tid)},
        )
        await repo.add_row(
            action="kb.delete", actor_user_id=None, actor_email="a@e.com",
            target_type="kb", target_id="kb_1", target_name="Second",
            outcome="success", ip_address="1.1.1.1", user_agent="UA",
            request_id="req-2", meta=None,
        )
        await s.commit()
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tid)},
        )

        rows = await repo.list_for_tenant()
        # Newest-first (DESC on created_at, audit_id): kb.delete inserted last.
        assert [r.action for r in rows] == ["kb.delete", "workflow.delete"]
        # tenant_id auto-filled from the GUC.
        assert all(r.tenant_id == tid for r in rows)
        # meta=None coerced to {}.
        assert rows[0].meta == {}
        assert rows[1].meta == {"k": "v"}

        # Filter by action narrows.
        only_wf = await repo.list_for_tenant(action="workflow.delete")
        assert [r.action for r in only_wf] == ["workflow.delete"]

        await s.rollback()
