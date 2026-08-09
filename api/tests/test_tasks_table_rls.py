"""Forced RLS isolation on tasks and task events.

Same pattern as ``test_rls_policies.py``: the ``app_engine``
fixture connects as the non-superuser ``vibecanvas_app`` role (the
table owner); ``FORCE ROW LEVEL SECURITY`` binds even the owner, so
cross-tenant rows are invisible.
"""
import uuid

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_tasks_blocks_cross_tenant(app_engine):
    """A task written under tenant A is invisible to tenant B."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    u_a = uuid.uuid4()

    # Seed two tenants + a user owned by A (auth tables have no RLS).
    async with app_engine.begin() as c:
        for t in (t_a, t_b):
            await c.execute(
                text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
                {"t": t},
            )
        await c.execute(
            text("INSERT INTO users(user_id, tenant_id, email) "
                 "VALUES (:u, :t, :e)"),
            {"u": u_a, "t": t_a, "e": f"a-{uuid.uuid4().hex[:8]}@example.com"},
        )

    # Write a task under tenant A.
    task_id = uuid.uuid4()
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.repo_tasks import TasksRepo

    async with session_scope(tenant_id=str(t_a)) as session:
        await TasksRepo(session).create(
            task_id=task_id,
            tenant_id=t_a,
            user_id=u_a,
            workflow_id=None,
            task_type="batch_exec",
            payload={},
        )

    # As tenant B → must see zero.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_b)},
        )
        rows = (await c.execute(text("SELECT id FROM tasks"))).all()
    assert rows == [], f"RLS leak — tenant B saw tenant A's tasks: {rows}"

    # As tenant A → sees its own row.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        rows = (await c.execute(text("SELECT id FROM tasks"))).all()
    assert [r[0] for r in rows] == [task_id]


@pytest.mark.asyncio
async def test_task_events_blocks_cross_tenant(app_engine):
    """A task_event written under tenant A is invisible to tenant B."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    u_a = uuid.uuid4()
    task_id = uuid.uuid4()

    async with app_engine.begin() as c:
        for t in (t_a, t_b):
            await c.execute(
                text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
                {"t": t},
            )
        await c.execute(
            text("INSERT INTO users(user_id, tenant_id, email) "
                 "VALUES (:u, :t, :e)"),
            {"u": u_a, "t": t_a, "e": f"b-{uuid.uuid4().hex[:8]}@example.com"},
        )

    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.repo_tasks import TasksRepo

    async with session_scope(tenant_id=str(t_a)) as session:
        repo = TasksRepo(session)
        await repo.create(
            task_id=task_id,
            tenant_id=t_a,
            user_id=u_a,
            workflow_id=None,
            task_type="batch_exec",
            payload={},
        )
        await repo.insert_event(task_id, "state", {}, t_a)

    # As tenant B → must see zero.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_b)},
        )
        rows = (await c.execute(text("SELECT id FROM task_events"))).all()
    assert rows == [], f"RLS leak — tenant B saw tenant A's events: {rows}"
