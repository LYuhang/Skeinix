"""Deployment cron dispatcher CAS + deployment worker enqueue."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import text

from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


async def _seed_cron(pg_engine, app_engine, *, cron_expr="* * * * *", cron_tz="UTC"):
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    dep_id = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
                        {"t": tenant_id})
        await c.execute(text(
            "INSERT INTO users(user_id, tenant_id, email) VALUES (:u, :t, :e)"
        ), {"u": user_id, "t": tenant_id, "e": f"c-{uuid.uuid4().hex[:6]}@example.com"})
    async with session_scope(tenant_id=str(tenant_id)) as session:
        await WorkflowRepo(session, str(user_id)).create_workflow(
            wf_id=wf_id,
            name="Cron Workflow",
        )
        await session.execute(text(
            "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, name, slug, "
            "trigger_type, version_pin, pinned_major, pinned_sub, cron_expr, cron_tz) "
            "VALUES (:id, :t, :u, :u, :w, 'C', :s, 'cron', 'head', NULL, NULL, :ce, :ctz)"
        ), {"id": dep_id, "t": tenant_id, "u": user_id, "w": wf_id,
             "s": f"cron-{uuid.uuid4().hex[:6]}", "ce": cron_expr, "ctz": cron_tz})
    return tenant_id, dep_id, user_id, wf_id


@pytest.mark.asyncio
async def test_cas_blocks_double_fire_same_next_fire(pg_engine, app_engine, monkeypatch, pg_url):
    from vibecanvas_api.celery_tasks.cron_dispatcher import _attempt_fire
    from vibecanvas_api.storage import db as db_mod
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    _, dep_id, _, _ = await _seed_cron(pg_engine, app_engine)
    nf = datetime.now(timezone.utc)
    assert await _attempt_fire(dep_id, nf) is True
    assert await _attempt_fire(dep_id, nf) is False


@pytest.mark.asyncio
async def test_cas_allows_later_next_fire(pg_engine, app_engine, monkeypatch, pg_url):
    from vibecanvas_api.celery_tasks.cron_dispatcher import _attempt_fire
    from vibecanvas_api.storage import db as db_mod
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    _, dep_id, _, _ = await _seed_cron(pg_engine, app_engine)
    t0 = datetime.now(timezone.utc) - timedelta(minutes=5)
    t1 = datetime.now(timezone.utc)
    assert await _attempt_fire(dep_id, t0) is True
    assert await _attempt_fire(dep_id, t1) is True
    assert await _attempt_fire(dep_id, t0) is False  # earlier than the new last_fire_at


@pytest.mark.asyncio
async def test_dispatch_sends_without_task_center_row(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.celery_tasks import cron_dispatcher
    from vibecanvas_api.storage import db as db_mod
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, dep_id, user_id, wf_id = await _seed_cron(
        pg_engine, app_engine, cron_expr="* * * * *",
    )
    # Backdate created_at so anchor → next_fire is past.
    async with pg_engine.begin() as c:
        await c.execute(text(
            "UPDATE deployments SET created_at = now() - interval '5 minutes' "
            "WHERE id = :id"
        ), {"id": dep_id})

    sent = []
    monkeypatch.setattr(
        cron_dispatcher.celery_app, "send_task",
        lambda *a, **kw: sent.append(kw),
    )

    await cron_dispatcher._dispatch()

    deployment_ids = [kw["kwargs"]["deployment_id"] for kw in sent]
    assert str(dep_id) in deployment_ids

    async with pg_engine.connect() as c:
        rows = (await c.execute(text(
            "SELECT task_type, status, deployment_id FROM tasks "
            "WHERE deployment_id = :did"
        ), {"did": dep_id})).all()
    # Migration 033 keeps deployment background invocations outside the
    # user-facing Task Center. Scheduled-run tasks belong to the separate
    # task_schedules control plane, not deployment cron triggers.
    assert rows == []


def test_beat_schedule_registered():
    import vibecanvas_api.celery_tasks.cron_dispatcher  # noqa: F401
    from vibecanvas_api.celery_app import celery_app
    assert "deployments.cron_dispatcher" in celery_app.conf.beat_schedule


def test_dispatcher_is_celery_task():
    from vibecanvas_api.celery_tasks.cron_dispatcher import dispatch_due_crons
    assert hasattr(dispatch_due_crons, "delay")
    assert dispatch_due_crons.name == "deployments.cron_dispatcher"
