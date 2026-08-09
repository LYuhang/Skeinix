import asyncio
import uuid

import pytest
from sqlalchemy import select, text
from vibecanvas_api.storage.models import Workflow, WorkflowVersion
from vibecanvas_api.storage.workflow_repo import WorkflowRepo

# WorkflowRepo stamps rows with the authenticated user id, which in production
# is a UUID string (AuthContext.user_id = str(sessions.user_id)) and is an
# FK to users.user_id. The `workflows`/`workflow_versions` tenant_id column
# resolves from the `app.tenant_id` GUC server-default. So a repo test must
# seed a tenant + the user rows and bind the session's tenant GUC.
TENANT = uuid.uuid4()
ALICE = uuid.uuid5(uuid.NAMESPACE_DNS, "alice")
BOB = uuid.uuid5(uuid.NAMESPACE_DNS, "bob")


async def _seed_and_bind(session):
    """Seed the tenant + alice/bob users (idempotent) and bind ``app.tenant_id``
    on this session so the tenant_id server-default resolves.

    pg_session/pg_engine connect as the RLS-bypassing superuser, so a plain
    INSERT works; the GUC is still required because tenant_id's DEFAULT reads
    ``current_setting('app.tenant_id')``.
    """
    await session.execute(
        text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'repo-test') "
             "ON CONFLICT (tenant_id) DO NOTHING"),
        {"t": TENANT},
    )
    for uid, email in ((ALICE, "alice@test"), (BOB, "bob@test")):
        await session.execute(
            text("INSERT INTO users(user_id, tenant_id, email) "
                 "VALUES (:u, :t, :e) ON CONFLICT (user_id) DO NOTHING"),
            {"u": uid, "t": TENANT, "e": email},
        )
    # Mirror db.py session_scope: set_config (NOT `SET`) accepts a bound param.
    # is_local=false → the GUC survives across this session's commits (the
    # concurrent test commits mid-transaction).
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(TENANT)}
    )


@pytest.mark.asyncio
async def test_create_and_get(pg_session):
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, user_id=str(ALICE))
    meta = await repo.create_workflow(name="My WF")
    wf_id = meta["wf_id"]
    assert meta["workflow_name"] == "My WF"
    got = await repo.get_meta(wf_id)
    assert got["wf_id"] == wf_id


@pytest.mark.asyncio
async def test_commit_bumps_sub_and_moves_head(pg_session):
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, user_id=str(ALICE))
    wf_id = (await repo.create_workflow(name="W"))["wf_id"]
    await repo.commit(wf_id, {"node_1": {"node_type": "StartNode"}}, note="c1")
    await repo.commit(wf_id, {"node_1": {"node_type": "StartNode"}, "node_2": {}}, note="c2")
    meta = await repo.get_meta(wf_id)
    assert (meta["active_major"], meta["active_sub"]) == (1, 2)
    wf = await repo.get_current_workflow(wf_id)
    assert "node_2" in wf


@pytest.mark.asyncio
async def test_workflow_versions_are_ciphertext_only(pg_session):
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, user_id=str(ALICE))
    wf_id = (await repo.create_workflow(name="Encrypted"))["wf_id"]
    workflow = {"node_1": {"node_type": "StartNode", "private": "value"}}
    pointer = await repo.commit(wf_id, workflow, note="encrypted")

    row = (
        await pg_session.execute(
            select(WorkflowVersion).where(
                WorkflowVersion.wf_id == wf_id,
                WorkflowVersion.major == 1,
                WorkflowVersion.sub == pointer.sv,
            )
        )
    ).scalar_one()
    assert row.workflow_ciphertext
    assert row.workflow_nonce
    assert row.workflow_key_id is not None
    assert await repo.get_current_workflow(wf_id) == workflow


@pytest.mark.asyncio
async def test_workflow_display_metadata_and_version_notes_are_ciphertext_only(
    pg_session,
):
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, user_id=str(ALICE))
    title = "private-workflow-title-sentinel"
    note = "private-version-note-sentinel"
    wf_id = (
        await repo.create_workflow(
            name=title,
            description="private workflow description",
            tags=["private-tag"],
        )
    )["wf_id"]
    pointer = await repo.commit(wf_id, {"node": {}}, note=note)

    workflow_row = await pg_session.get(Workflow, wf_id)
    version_row = await pg_session.get(
        WorkflowVersion,
        {"wf_id": wf_id, "major": 1, "sub": pointer.sv},
    )
    assert workflow_row is not None and workflow_row.metadata_ciphertext
    assert title not in workflow_row.metadata_ciphertext
    assert version_row is not None and version_row.note_ciphertext
    assert note not in version_row.note_ciphertext

    columns = set((await pg_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='workflows'"
    ))).scalars())
    assert {"workflow_name", "description", "tags"}.isdisjoint(columns)
    version_columns = set((await pg_session.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='workflow_versions'"
    ))).scalars())
    assert "note" not in version_columns

    assert (await repo.get_meta(wf_id))["workflow_name"] == title
    history = await repo.get_version_history(wf_id)
    assert next(item for item in history if item["sub"] == pointer.sv)["note"] == note


@pytest.mark.asyncio
async def test_concurrent_commits_atomic_sv(pg_session, pg_engine):
    """Two concurrent commits must not collide on (wf_id, major, sub)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, user_id=str(BOB))
    wf_id = (await repo.create_workflow(name="C"))["wf_id"]
    await pg_session.commit()
    sm = async_sessionmaker(pg_engine, expire_on_commit=False)

    async def do_commit(i):
        async with sm() as s:
            await s.execute(
                text("SELECT set_config('app.tenant_id', :t, false)"),
                {"t": str(TENANT)},
            )
            r = WorkflowRepo(s, user_id=str(BOB))
            await r.commit(wf_id, {"k": i}, note=f"c{i}")
            await s.commit()

    await asyncio.gather(*[do_commit(i) for i in range(5)])
    async with sm() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(TENANT)},
        )
        r = WorkflowRepo(s, user_id=str(BOB))
        history = await r.get_version_history(wf_id)
    subs = sorted(h["sub"] for h in history if h["major"] == 1)
    assert subs == [0, 1, 2, 3, 4, 5]  # sv0 from create + 5 commits, no dupes
