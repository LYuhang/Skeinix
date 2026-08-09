"""OrganizationRepo / GroupRepo — tenant-bound AsyncSession repos.

Mirrors ``test_routes_llm_credentials.py``: seed tenant+user via the
RLS-bypassing superuser ``pg_engine``, replay migration 022's backfill so the
personal org + owner membership exist, then exercise the repos through a
``session_scope(tenant_id=...)`` session (sets the ``app.tenant_id`` GUC so RLS
applies).
"""
import importlib.util
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_org import GroupRepo, OrganizationRepo

pytestmark = pytest.mark.asyncio


def _backfill_statements():
    path = (Path(__file__).resolve().parent.parent / "alembic" / "versions"
            / "022_org_permissions_foundation.py")
    spec = importlib.util.spec_from_file_location("_mig022_repo", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.BACKFILL_SQL


async def _seed_tenant_user_and_backfill(pg_engine):
    t = uuid.uuid4()
    u = uuid.uuid4()
    async with pg_engine.begin() as conn:
        await conn.execute(text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'Acme')"), {"t": t})
        await conn.execute(text(
            "INSERT INTO users(user_id, tenant_id, email) VALUES (:u, :t, :e)"),
            {"u": u, "t": t, "e": f"{u}@x.test"})
        for stmt in _backfill_statements():
            # Current head intentionally removed the legacy grant table.
            # Only replay the retained organization/owner backfill pieces.
            if "resource_grants" in stmt:
                continue
            await conn.execute(text(stmt))
    return t, u


async def test_org_repo_get_returns_personal_org(pg_engine):
    t, _u = await _seed_tenant_user_and_backfill(pg_engine)
    async with session_scope(tenant_id=str(t)) as s:
        org = await OrganizationRepo(s).get_current()
    assert org is not None
    assert org.kind == "personal"


async def test_auth_repo_lists_only_users_organizations(pg_engine):
    t, u = await _seed_tenant_user_and_backfill(pg_engine)
    async with session_scope() as s:
        rows = await AuthRepo(s).list_organizations_for_user(u)
    assert rows == [
        {
            "organization_id": str(t),
            "kind": "personal",
            "slug": f"org-{str(t).replace('-', '')}",
            "name": "Acme",
            "membership_id": rows[0]["membership_id"],
            "role": "owner",
            "status": "active",
        }
    ]


async def test_group_repo_creates_generic_group(pg_engine):
    t, u = await _seed_tenant_user_and_backfill(pg_engine)
    async with session_scope(tenant_id=str(t)) as s:
        group = await GroupRepo(s).create(
            organization_id=t,
            created_by=u,
            name="Engineering",
            kind="department",
            parent_group_id=None,
        )
        group_id = group.group_id
    async with session_scope(tenant_id=str(t)) as s:
        stored = await GroupRepo(s).get(group_id)
    assert stored is not None
    assert stored.kind == "department"
