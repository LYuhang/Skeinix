"""Guard: ``SyncWorkflowRepo`` must survive ≥2 consecutive calls.

The synchronous agent calls the repository facade
3× per auto-save turn (``agent.py``: commit→mark_saved→
get_current_workflow) from a worker thread with NO running event loop.

Pre-fix bug: ``SyncWorkflowRepo`` went through the process-global
``db.py:init_engine``/``session_scope``. That engine binds its asyncpg
pool to the FIRST ``asyncio.run`` call's event loop; call #2 opens a
fresh loop but reuses connections owned by the now-closed first loop →
``RuntimeError: Event loop is closed`` / "attached to a different loop"
(reproduced by the reviewer; previously masked by the blanket
``except Exception: pytest.skip`` in test_agent_async_smoke.py).

Post-fix: each call builds its own ``NullPool`` engine and disposes it
inside the same ``asyncio.run``, so no loop-bound state survives.

Test shape rationale: a plain sync test can't cleanly depend on the
async session fixtures (they require a running loop). So this is an
``asyncio``-mode test that seeds a workflow via the async ``pg_session``,
then drives the ≥2 ``SyncWorkflowRepo`` calls inside
``await asyncio.to_thread(...)`` — a threadpool thread has no running
loop, exactly matching the real ``routes/refs.py`` / agent-thread
context the facade is used from. Asserting no ``RuntimeError`` and that
both the 1st and the 3rd call return correct data proves the fix:
pre-fix this FAILS on call #2 with "Event loop is closed".
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import text

from vibecanvas_api.storage.sync_repo import SyncWorkflowRepo
from vibecanvas_api.storage.sync_session import current_sync_tenant_id
from vibecanvas_api.storage.workflow_repo import WorkflowRepo

# WorkflowRepo / SyncWorkflowRepo stamp rows with the authenticated user id,
# which in production is a UUID string (FK to users.user_id). The workflow
# tenant_id column resolves from the `app.tenant_id` GUC server-default. So the
# test must seed a tenant + user row and bind the session's tenant GUC. The
# SyncWorkflowRepo facade opens its OWN short NullPool session per call, so the
# tenant/user rows must be COMMITTED (visible to a separate connection) and the
# facade itself binds the GUC inside run_in_short_session via session_scope —
# we pass the same UUID string the facade will stamp.
# (mirrors test_workflow_repo_pg.py / test_execution_repo_pg.py)
TENANT = uuid.uuid4()
USER = uuid.uuid5(uuid.NAMESPACE_DNS, "sync-repo-user")


async def _seed_and_bind(session):
    await session.execute(
        text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'sync-repo-test') "
             "ON CONFLICT (tenant_id) DO NOTHING"),
        {"t": TENANT},
    )
    await session.execute(
        text("INSERT INTO users(user_id, tenant_id, email) "
             "VALUES (:u, :t, :e) ON CONFLICT (user_id) DO NOTHING"),
        {"u": USER, "t": TENANT, "e": "sync-repo@test"},
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(TENANT)}
    )


async def test_sync_repo_survives_consecutive_calls(pg_session):
    # Seed a workflow + an initial commit through the async repo, then
    # COMMIT so the facade's independent short session sees it (separate
    # connection — mirrors test_workflow_repo_pg.py).
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, str(USER))
    wf_id = (await repo.create_workflow(name="sync-guard"))["wf_id"]
    await repo.commit(wf_id, {"node_1": {"node_type": "StartNode"}},
                      note="init")
    await pg_session.commit()

    def hot_path() -> tuple[dict, dict]:
        """Mirror the agent auto-save hot path: 3 consecutive sync
        calls from a thread with no running event loop."""
        r = SyncWorkflowRepo(username=str(USER))
        first = r.get_current_workflow(wf_id)          # call #1
        r.commit(wf_id, {"node_1": {"node_type": "StartNode"},
                         "node_2": {"node_type": "EndNode"}},
                 note="auto-save")                      # call #2
        r.mark_saved(wf_id)                             # call #3
        third = r.get_current_workflow(wf_id)           # call #4
        return first, third

    # Bind the tenant the facade's short sessions will isolate to (RLS +
    # tenant_id DEFAULT). The ContextVar is copied into asyncio.to_thread's
    # worker context, mirroring how agent.run_agent_turn sets it.
    current_sync_tenant_id.set(str(TENANT))
    # asyncio.to_thread → a threadpool thread with NO running loop,
    # exactly the real facade call context.
    first, third = await asyncio.to_thread(hot_path)

    assert "node_1" in first and "node_2" not in first, (
        f"call #1 wrong content: {first!r}"
    )
    assert "node_2" in third, (
        f"call #4 (post-commit) did not see the 2nd call's write: {third!r}"
    )


async def test_sync_repo_two_calls_minimal(pg_session):
    """Minimal ≥2-call variant (create-then-get) — also fails pre-fix
    on the 2nd call."""
    await _seed_and_bind(pg_session)
    repo = WorkflowRepo(pg_session, str(USER))
    wf_id = (await repo.create_workflow(name="min"))["wf_id"]
    await pg_session.commit()

    def two_calls() -> tuple[dict, list]:
        r = SyncWorkflowRepo(username=str(USER))
        m = r.get_meta(wf_id)                       # call #1
        majors = r.list_major_versions(wf_id)       # call #2
        return m, majors

    current_sync_tenant_id.set(str(TENANT))
    meta, majors = await asyncio.to_thread(two_calls)
    assert meta["wf_id"] == wf_id
    assert majors == [{"v": 1, "sv": 0, "label": "v1.0"}]
