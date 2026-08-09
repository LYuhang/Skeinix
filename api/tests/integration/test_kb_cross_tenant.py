"""KB / RAG T12.2 — Cross-tenant RLS invariant.

Tenant A creates a KB; the same row MUST NOT appear when Tenant B
opens its own ``session_scope(tenant_id=...)`` and reads either
``list_kbs`` or ``get_kb(kb_id_from_a)``. Verifies FORCE-RLS on the
``knowledge_bases`` table (migration 003 + 008) is in force end-to-end
through the route handlers — not just at the repo layer.

This test does NOT exercise auth headers; route handlers receive a
stub ``AuthContext`` and the tenant is bound via the
``session_scope(tenant_id=...)`` GUC. RLS is what does the filtering,
so this is the most direct way to assert the invariant.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.routes.kb import (
    KbCreate,
    create_kb,
    get_kb,
    list_kbs,
)
from vibecanvas_api.storage.db import session_scope


# --------------------------------------------------------------------- seed


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
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
             "e": f"kb-cross-{uuid.uuid4().hex[:6]}@example.com"},
        )


class _StubCtx:
    def __init__(self, tenant_id, user_id):
        self.tenant_id = str(tenant_id)
        self.user_id = str(user_id)
        self.active_organization_id = str(tenant_id)
        self.session_id = "test-session"
        self.session_generation = 1
        self.membership_id = "test-membership"
        self.membership_role = "owner"
        self.membership_status = "active"
        self.authentication_strength = "password"
        self.email = "stub@example.com"


class _StubRequest:
    def __init__(self):
        self.headers = {}
        self.client = None
        self.state = SimpleNamespace(request_id="kb-cross-tenant")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    def __init__(self, resource_ids=()):
        self._resource_ids = tuple(str(value) for value in resource_ids)

    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")

    async def list_authorized_ids(self, *args, **kwargs):
        return self._resource_ids

    async def batch_check(self, checks):
        return tuple(
            Decision(allowed=True, reason_code="test_fixture")
            for _ in checks
        )


# --------------------------------------------------------------------- test


@pytest.mark.asyncio
async def test_tenant_b_cannot_see_tenant_a_kb(pg_engine):
    """A→creates KB; B→cannot list or fetch by id."""
    tenant_a = uuid.uuid4()
    user_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_b = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_a, user_a)
    await _seed_tenant_and_user(pg_engine, tenant_b, user_b)

    ctx_a = _StubCtx(tenant_a, user_a)
    ctx_b = _StubCtx(tenant_b, user_b)

    # Tenant A creates a KB.
    async with session_scope(tenant_id=str(tenant_a)) as s:
        kb_a = await create_kb(
            body=KbCreate(name="Secret"), request=_StubRequest(),
            ctx=ctx_a, session=s, service=_AllowAuthz(),
        )
        await s.commit()
        kb_id_a = uuid.UUID(kb_a.id)

    # Tenant B's list does NOT include A's KB.
    async with session_scope(tenant_id=str(tenant_b)) as s:
        b_list = await list_kbs(
            request=_StubRequest(), ctx=ctx_b, session=s,
            service=_AllowAuthz((kb_id_a,)),
        )
    assert not any(k.id == kb_a.id for k in b_list), (
        f"RLS leak: tenant_b saw tenant_a's KB in list: "
        f"{[k.id for k in b_list]} (expected {kb_a.id} hidden)"
    )

    # Tenant B's get_kb(A's kb_id) → 404 kb_not_found.
    async with session_scope(tenant_id=str(tenant_b)) as s:
        with pytest.raises(HTTPException) as exc_info:
            await get_kb(
                kb_id=kb_id_a, request=_StubRequest(), ctx=ctx_b, session=s,
                service=_AllowAuthz(),
            )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "kb_not_found"

    # And A still sees its own KB (sanity — RLS isn't blocking A too).
    async with session_scope(tenant_id=str(tenant_a)) as s:
        a_list = await list_kbs(
            request=_StubRequest(), ctx=ctx_a, session=s,
            service=_AllowAuthz((kb_id_a,)),
        )
    assert any(k.id == kb_a.id for k in a_list), (
        f"tenant_a lost visibility of its own KB: {[k.id for k in a_list]}"
    )
