"""Gate 3 — storage round-trip works against the migrated WorkflowRepo.

Ported from the legacy synchronous filesystem API to the
Postgres async API (constructor ``(session, username)``, every method
``await``-ed), using the ``pg_session`` fixture exactly like
``test_workflow_repo_pg.py``. Original intent preserved: (1) create +
commit + read-back round-trip, (2) two subversions then branch from
sv=0 with parent pointers.

⚠️ ``pg_session`` expires all instances after every commit (conftest
hook, stricter than production) — re-query via the repo's own async
methods, never read a stale mapped attribute after commit.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.storage.workflow_repo import WorkflowRepo

# WorkflowRepo stamps rows with the authenticated user id (a UUID FK to
# users.user_id); workflows.tenant_id resolves from the `app.tenant_id` GUC
# server-default. So a repo test must seed a tenant + user row and bind the
# session's tenant GUC (mirrors test_workflow_repo_pg.py).
TENANT = uuid.uuid4()
USER = uuid.uuid5(uuid.NAMESPACE_DNS, "storage-smoke-user")


async def _seed_and_bind(session):
    await session.execute(
        text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'smoke-test') "
             "ON CONFLICT (tenant_id) DO NOTHING"),
        {"t": TENANT},
    )
    await session.execute(
        text("INSERT INTO users(user_id, tenant_id, email) "
             "VALUES (:u, :t, :e) ON CONFLICT (user_id) DO NOTHING"),
        {"u": USER, "t": TENANT, "e": "smoke@test"},
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(TENANT)}
    )


@pytest.mark.asyncio
async def test_workflow_repo_round_trip(pg_session):
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, str(USER))
    meta = await repo.create_workflow(name="smoke_wf", description="t")
    wf_id = meta["wf_id"]

    # create_workflow seeds sv0; the first explicit commit allocates sv1.
    ptr = await repo.commit(wf_id, {"__meta__": {}}, note="initial")
    assert ptr.sv == 1

    fetched = await repo.get_workflow_at(wf_id, v=1, sv=1)
    assert fetched is not None
    assert "__meta__" in fetched


@pytest.mark.asyncio
async def test_workflow_repo_branching(pg_session):
    """Commit two subversions, then branch from sv=0 and verify
    parent pointers (the version-tree v1 contract)."""
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, str(USER))
    meta = await repo.create_workflow(name="branch_smoke")
    wf_id = meta["wf_id"]

    # sv0 is seeded by create_workflow; these allocate sv1 and sv2.
    await repo.commit(wf_id, {"__meta__": {}}, note="sv1")
    await repo.commit(wf_id, {"__meta__": {}}, note="sv2")

    # HEAD checkout back to sv=0, then a fresh commit must branch off it:
    # the new sv's parent_sv is the (re-pointed) HEAD sv (=0).
    await repo.set_head(wf_id, major=1, sub=0)

    branch_ptr = await repo.commit(wf_id, {"__meta__": {}}, note="branch")
    assert branch_ptr.sv == 3
    assert branch_ptr.parent_sv == 0
