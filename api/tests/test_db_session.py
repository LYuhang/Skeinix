import uuid

import pytest
from sqlalchemy import text

# Workflow.creator_user_id is a UUID FK to users.user_id, and workflows.tenant_id
# resolves from the `app.tenant_id` GUC server-default. So a direct-ORM test must
# seed a tenant + user row and bind the session's tenant GUC (mirrors
# test_workflow_repo_pg.py).
TENANT = uuid.uuid4()
USER = uuid.uuid5(uuid.NAMESPACE_DNS, "db-session-user")


async def _seed_and_bind(session):
    await session.execute(
        text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'db-session-test') "
             "ON CONFLICT (tenant_id) DO NOTHING"),
        {"t": TENANT},
    )
    await session.execute(
        text("INSERT INTO users(user_id, tenant_id, email) "
             "VALUES (:u, :t, :e) ON CONFLICT (user_id) DO NOTHING"),
        {"u": USER, "t": TENANT, "e": "db-session@test"},
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(TENANT)}
    )


@pytest.mark.asyncio
async def test_migration_creates_all_tables(pg_engine):
    async with pg_engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        names = {r[0] for r in rows}
    assert {
        "workflows",
        "workflow_versions",
        "chats",
        "chat_messages",
        "templates",
        "workflow_run_state",
        "workflow_run_events",
        "agent_runs",
        "agent_run_events",
    } <= names


@pytest.mark.asyncio
async def test_updated_at_trigger_fires(pg_session):
    from vibecanvas_api.storage.models import Workflow
    from vibecanvas_api.storage.workflow_repo import WorkflowRepo
    import asyncio
    await _seed_and_bind(pg_session)
    await WorkflowRepo(pg_session, str(USER)).create_workflow(
        wf_id="w1",
        name="a",
    )
    await pg_session.commit()
    before = (await pg_session.get(Workflow, "w1")).updated_at
    await asyncio.sleep(0.01)
    wf2 = await pg_session.get(Workflow, "w1")
    wf2.status = "published"
    await pg_session.commit()
    after = (await pg_session.get(Workflow, "w1")).updated_at
    assert after > before
