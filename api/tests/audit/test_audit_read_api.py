"""T6 — GET /api/v1/audit: tenant-scoped, filters, cursor.

Strategy: handler-direct-call (mirrors ``test_audit_resource_hooks.py`` —
the TestClient ride over the real ``current_user`` Bearer DI is fragile in this
repo). We seed ``audit_log`` rows directly via the superuser engine (RLS-bypass,
explicit tenant_id), then invoke ``list_audit`` with a real ``vibecanvas_app``
``session_scope(tenant_id=...)`` session and a ``_StubCtx`` standing in for the
``current_user`` dependency. RLS scoping is exercised end-to-end because the
read goes through the tenant-bound session GUC.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision


# --------------------------------------------------------------------- stubs


class _StubCtx:
    """Stand-in for ``AuthContext`` (current_user). list_audit reads nothing off
    it — RLS does the scoping — but the signature requires it."""

    def __init__(self, tenant_id, user_id, email="reader@example.com"):
        self.tenant_id = str(tenant_id)
        self.user_id = str(user_id)
        self.active_organization_id = str(tenant_id)
        self.session_id = "audit-read-session"
        self.session_generation = 1
        self.membership_id = "audit-read-membership"
        self.membership_role = "auditor"
        self.membership_status = "active"
        self.authentication_strength = "password"
        self.email = email


class _StubRequest:
    state = SimpleNamespace(request_id="audit-read-test")


class _AllowAuthz:
    async def check(self, *_args, **_kwargs):
        return Decision(allowed=True, reason_code="test_fixture")


class _DenyAuthz:
    async def check(self, *_args, **_kwargs):
        return Decision(allowed=False, reason_code="test_fixture")


# --------------------------------------------------------------------- seeds


async def _seed_tenant(pg_engine):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tenant_id},
        )
        await c.execute(
            text("INSERT INTO users(user_id, tenant_id, email) VALUES (:u, :t, :e)"),
            {"u": user_id, "t": tenant_id, "e": f"t6-{uuid.uuid4().hex[:6]}@example.com"},
        )
    return tenant_id, user_id


async def _seed_audit_row(
    pg_engine, tenant_id, *, action, outcome="success", created_at, target_name=None
):
    """Insert one audit row via the superuser engine (RLS-bypass, explicit
    tenant_id + created_at so the keyset order is deterministic)."""
    aid = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO audit_log "
                "(audit_id, tenant_id, action, outcome, target_name, created_at) "
                "VALUES (:a, :t, :act, :o, :tn, :ts)"
            ),
            {
                "a": aid,
                "t": tenant_id,
                "act": action,
                "o": outcome,
                "tn": target_name,
                "ts": created_at,
            },
        )
    return aid


def _call(session, ctx, *, service=None, **kw):
    from vibecanvas_api.routes.audit import list_audit

    params = dict(
        action=None,
        outcome=None,
        ts_from=None,
        ts_to=None,
        cursor=None,
        limit=50,
    )
    params.update(kw)
    return list_audit(
        request=_StubRequest(),
        ctx=ctx,
        session=session,
        service=service or _AllowAuthz(),
        **params,
    )


@pytest.mark.asyncio
async def test_list_requires_the_organization_view_audit_capability() -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    with pytest.raises(HTTPException) as denied:
        await _call(None, _StubCtx(tenant_id, user_id), service=_DenyAuthz())
    assert denied.value.status_code == 404
    assert denied.value.detail == "resource_not_found"


# ----------------------------------------------------------- tenant isolation


@pytest.mark.asyncio
async def test_list_returns_only_my_tenant_newest_first(pg_engine):
    """Items are DESC by created_at and only the caller's tenant is visible."""
    from vibecanvas_api.storage.db import session_scope

    ta, ua = await _seed_tenant(pg_engine)
    tb, _ = await _seed_tenant(pg_engine)
    base = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)

    # tenant A: three rows at increasing times (so newest = +2m).
    for i, act in enumerate(["auth.logout", "workflow.delete", "deployment.create"]):
        await _seed_audit_row(
            pg_engine,
            ta,
            action=act,
            created_at=base + timedelta(minutes=i),
        )
    # tenant B: a row that must NOT show up for A.
    await _seed_audit_row(
        pg_engine,
        tb,
        action="kb.delete",
        created_at=base + timedelta(hours=1),
    )

    async with session_scope(tenant_id=str(ta)) as s:
        out = await _call(s, _StubCtx(ta, ua))

    actions = [it["action"] for it in out["items"]]
    assert actions == ["deployment.create", "workflow.delete", "auth.logout"]
    assert "kb.delete" not in actions  # tenant B isolated
    assert out["next_cursor"] is None  # only 3 rows, default limit 50


# --------------------------------------------------------------- filters


@pytest.mark.asyncio
async def test_filter_by_action_and_outcome(pg_engine):
    from vibecanvas_api.storage.db import session_scope

    ta, ua = await _seed_tenant(pg_engine)
    base = datetime(2026, 5, 30, 9, 0, tzinfo=timezone.utc)
    await _seed_audit_row(
        pg_engine, ta, action="workflow.delete", outcome="success", created_at=base
    )
    await _seed_audit_row(
        pg_engine,
        ta,
        action="workflow.delete",
        outcome="failure",
        created_at=base + timedelta(minutes=1),
    )
    await _seed_audit_row(
        pg_engine,
        ta,
        action="deployment.create",
        outcome="success",
        created_at=base + timedelta(minutes=2),
    )

    async with session_scope(tenant_id=str(ta)) as s:
        only_wf = await _call(s, _StubCtx(ta, ua), action="workflow.delete")
        wf_success = await _call(
            s,
            _StubCtx(ta, ua),
            action="workflow.delete",
            outcome="success",
        )

    assert {it["action"] for it in only_wf["items"]} == {"workflow.delete"}
    assert len(only_wf["items"]) == 2
    assert len(wf_success["items"]) == 1
    assert wf_success["items"][0]["outcome"] == "success"


# --------------------------------------------------------------- cursor


@pytest.mark.asyncio
async def test_cursor_pagination_no_dupes(pg_engine):
    """limit=2, follow next_cursor; the union covers all rows with no dupes."""
    from vibecanvas_api.storage.db import session_scope

    ta, ua = await _seed_tenant(pg_engine)
    base = datetime(2026, 5, 30, 8, 0, tzinfo=timezone.utc)
    seeded = []
    for i in range(5):
        aid = await _seed_audit_row(
            pg_engine,
            ta,
            action="auth.logout",
            created_at=base + timedelta(minutes=i),
        )
        seeded.append(str(aid))

    collected = []
    cursor = None
    pages = 0
    async with session_scope(tenant_id=str(ta)) as s:
        while True:
            page = await _call(s, _StubCtx(ta, ua), cursor=cursor, limit=2)
            pages += 1
            collected.extend(it["audit_id"] for it in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
            assert pages < 10  # guard against an infinite loop

    assert len(collected) == 5
    assert len(set(collected)) == 5  # no dupes
    assert set(collected) == set(seeded)  # no gaps
    # newest-first across pages
    assert collected[0] == seeded[-1]
    assert collected[-1] == seeded[0]
