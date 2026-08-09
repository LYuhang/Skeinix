"""Row-level-security isolation tests for business tables.

Uses the ``app_engine`` fixture (conftest), which connects as the
non-superuser ``vibecanvas_app`` role that OWNS the business tables.
``FORCE ROW LEVEL SECURITY`` is what makes RLS apply to that owner — so
this test passes ONLY if migration 003's FORCE clauses are in place.
The workflow INSERT omits ``tenant_id`` on purpose: the column's
``DEFAULT current_setting('app.tenant_id', ...)`` must fill it.
"""
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


@pytest.mark.asyncio
async def test_rls_blocks_cross_tenant(app_engine):
    """Row written under tenant A is invisible under tenant B."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    async with app_engine.begin() as c:
        # auth tables have no RLS — seed tenants / a user directly
        for t in (t_a, t_b):
            await c.execute(text("INSERT INTO tenants(tenant_id,name) "
                                 "VALUES (:t,'x')"), {"t": t})
        u_a = uuid.uuid4()
        await c.execute(text("INSERT INTO users(user_id,tenant_id,email) "
                             "VALUES (:u,:t,'a@example.com')"), {"u": u_a, "t": t_a})
    # write a workflow as tenant A — tenant_id is NOT supplied; the
    # column's DEFAULT current_setting('app.tenant_id') must fill it.
    async with session_scope(tenant_id=str(t_a)) as session:
        await WorkflowRepo(session, str(u_a)).create_workflow(
            wf_id="w1",
            name="A wf",
        )
    # read as tenant B → must see nothing
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id',:t,false)"),
                        {"t": str(t_b)})
        rows = (await c.execute(text("SELECT wf_id FROM workflows"))).all()
    assert rows == []
    # read as tenant A → sees its row
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id',:t,false)"),
                        {"t": str(t_a)})
        rows = (await c.execute(text("SELECT wf_id FROM workflows"))).all()
    assert [r[0] for r in rows] == ["w1"]
