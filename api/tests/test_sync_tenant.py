"""The agent's synchronous data path carries the RLS tenant.

The agent (and the ref-resolve route) write to RLS-protected tables
through ``storage/sync_session.py:run_in_short_session``, which opens its
own short transaction OUTSIDE any request's ``tenant_db`` scope. Without
a tenant context the business tables' ``FORCE ROW LEVEL SECURITY`` +
``tenant_id DEFAULT current_setting('app.tenant_id', true)`` would leave
``tenant_id`` NULL → NOT-NULL violation, and reads would see only public
rows.

T9d threads the tenant through a ``ContextVar`` (``current_sync_tenant_id``)
set at the agent daemon-thread / ref-resolve boundary and read inside
``run_in_short_session``. These tests prove the mechanism:

1. ``run_in_short_session`` honours the CV — when set, the short
   session's ``app.tenant_id`` GUC equals it; when unset, it is empty.
2. A ``SyncWorkflowRepo`` write made under the CV lands the row with the
   right ``tenant_id`` and is RLS-isolated from another tenant.

``run_in_short_session`` builds its engine from ``config.database.url``,
which the conftest ``_migrate`` fixture points at the non-superuser
``vibecanvas_app`` role — so RLS genuinely applies here. Tests depend on
the ``app_engine`` fixture (which itself depends on ``_migrate``) so the
schema is migrated and the engine is the RLS-bound app role.
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.sync_repo import SyncWorkflowRepo
from vibecanvas_api.storage.sync_session import (
    current_sync_tenant_id, run_in_short_session,
)
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


def test_run_in_short_session_uses_tenant_contextvar(app_engine):
    """``run_in_short_session`` sets ``app.tenant_id`` from the CV.

    Sync test — ``run_in_short_session`` is synchronous (it drives its
    own ``asyncio.run``); it must NOT be marked ``@pytest.mark.asyncio``.
    The ``app_engine`` fixture is depended on only so ``_migrate`` has
    run (the schema/role exist) before the short session connects.
    """
    tenant_id = str(uuid.uuid4())

    async def _read_guc(s):
        return (await s.execute(
            text("SELECT current_setting('app.tenant_id', true)"))).scalar()

    # CV set → the short session's GUC equals it.
    token = current_sync_tenant_id.set(tenant_id)
    try:
        got = run_in_short_session(_read_guc)
    finally:
        current_sync_tenant_id.reset(token)
    assert got == tenant_id, (
        f"short session GUC {got!r} != CV tenant {tenant_id!r}")

    # CV unset (default None) → no set_config call → GUC is NULL/empty.
    assert current_sync_tenant_id.get() is None
    got_unset = run_in_short_session(_read_guc)
    assert got_unset in (None, ""), (
        f"GUC should be unset with no CV, got {got_unset!r}")


async def test_sync_repo_write_under_cv_is_tenant_scoped(app_engine):
    """A ``SyncWorkflowRepo`` write under the CV lands the row with the
    CV's ``tenant_id`` and is RLS-isolated from another tenant.

    Seeds two tenants + a user + a ``workflows`` row for tenant A, then
    drives ``SyncWorkflowRepo.commit`` from a worker thread (no running
    loop — exactly the agent-thread context) with the CV set to tenant
    A. The new ``workflow_versions`` row's ``tenant_id`` DEFAULT is
    filled from the CV-driven ``set_config``; it must be visible to
    tenant A and invisible to tenant B.
    """
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    u_a = uuid.uuid4()
    wf_id = "wf_t9d"

    # Seed tenants / user (auth tables — no RLS), then create the Workflow
    # through its strict encrypted repository boundary.
    async with app_engine.begin() as c:
        for t in (t_a, t_b):
            await c.execute(text("INSERT INTO tenants(tenant_id,name) "
                                 "VALUES (:t,'x')"), {"t": t})
        await c.execute(
            text("INSERT INTO users(user_id,tenant_id,email) "
                 "VALUES (:u,:t,'t9d@example.com')"), {"u": u_a, "t": t_a})
    async with session_scope(tenant_id=str(t_a)) as session:
        await WorkflowRepo(session, str(u_a)).create_workflow(
            wf_id=wf_id,
            name="A wf",
        )

    # Drive SyncWorkflowRepo.commit from a worker thread with the CV set
    # to tenant A — mirrors the agent daemon-thread boundary.
    def sync_write() -> None:
        token = current_sync_tenant_id.set(str(t_a))
        try:
            SyncWorkflowRepo(str(u_a)).commit(
                wf_id, {"node_1": {"node_type": "StartNode"}},
                note="t9d-write")
        finally:
            current_sync_tenant_id.reset(token)

    await asyncio.to_thread(sync_write)

    # Tenant A sees the new workflow_versions row with tenant_id == t_a.
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id',:t,false)"),
                        {"t": str(t_a)})
        rows = (await c.execute(text(
            "SELECT tenant_id, sub FROM workflow_versions "
            "WHERE wf_id=:w ORDER BY sub"), {"w": wf_id})).all()
    assert len(rows) == 2, f"tenant A should see sv0 + sync write, got {rows!r}"
    assert str(rows[-1][0]) == str(t_a), (
        f"written tenant_id {rows[-1][0]!r} != CV tenant {t_a!r}")
    async with session_scope(tenant_id=str(t_a)) as session:
        history = await WorkflowRepo(session, str(u_a)).get_version_history(wf_id)
    assert history[-1]["note"] == "t9d-write"

    # Tenant B must see nothing — RLS isolation.
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id',:t,false)"),
                        {"t": str(t_b)})
        rows_b = (await c.execute(text(
            "SELECT sub FROM workflow_versions WHERE wf_id=:w"),
            {"w": wf_id},
        )).all()
    assert rows_b == [], (
        f"tenant B must not see tenant A's sync write, got {rows_b!r}")
