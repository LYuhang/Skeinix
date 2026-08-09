"""Resubmit stuck queued tasks through the admin engine.

The atomic-submit route (POST /workflows/{wf_id}/batch) inserts the
``tasks`` row inside the request transaction, then does a best-effort
``celery_app.send_task`` after commit. If the broker call fails — or
the broker temporarily drops the delivery — the row stays ``queued``
forever and no worker picks it up.

A celery-beat scheduled job (registered below) sweeps every 30s for
``status='queued' AND submitted_at < now() - 60s`` and re-publishes the
task to the broker. Celery dedupes on ``task_id`` (== ``celery_id`` ==
``tasks.id`` per §6.3), so:

* a duplicate of a still-pending message is a broker-side no-op;
* a lost delivery triggers a fresh pick-up.

Uses the admin engine (RLS-bypassing) because this is a system-owned
cross-tenant sweep — there is no request context, no user, no tenant.

Only ``batch_exec`` rows are resubmitted here. Other background systems own
their own lifecycle and do not use the Task Center table.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from vibecanvas_api.celery_app import celery_app
from vibecanvas_api.storage.models_tasks import Task
from vibecanvas_api.storage.repo_tasks import TasksRepo
from vibecanvas_api.storage.sync_session import short_admin_session


RECONCILER_INTERVAL_SEC = 30
STUCK_THRESHOLD_SEC = 60


_TASK_TYPE_TO_CELERY_NAME: dict[str, str] = {
    "batch_exec": "batch_exec",
}


def _build_kwargs_for(task_type: str, row, payload: dict) -> dict:
    """Build the Celery ``kwargs`` dict for a stuck row by ``task_type``.

    Each task's worker signature is its own — they share NO kwargs
    contract, so the dispatch is explicit per branch. Keep this in
    lockstep with :data:`_TASK_TYPE_TO_CELERY_NAME`.

    Raises ``ValueError`` for an unknown ``task_type`` — the caller
    (:func:`_resubmit`) gates this via the dispatch table so it can
    quietly skip unmapped types; the raise here protects future callers
    that bypass the dispatch table.
    """
    if task_type == "batch_exec":
        return dict(
            task_id=row.celery_id,
            tenant_id=str(row.tenant_id),
            user_id=str(row.user_id),
            workflow_id=row.workflow_id,
            data_source=payload.get("data_source", {}),
            column_mapping=payload.get("column_mapping", {}),
        )
    raise ValueError(
        f"reconciler: no kwargs builder for task_type={task_type!r}"
    )


@celery_app.task(name="phase6.reconciler.resubmit_stuck_queued")
def resubmit_stuck_queued():
    """Celery entry point — runs the async sweep on a fresh event loop."""
    asyncio.run(_resubmit())


async def _resubmit() -> None:
    """Sweep ``tasks WHERE status='queued' AND stuck`` → re-publish.

    Read-only on the DB; the worker picking the re-published task is
    what flips ``status`` to ``running`` (no UPDATE here).

    The SELECT clause includes ``task_type`` (added in KB/RAG T6); the
    dispatch table + ``_build_kwargs_for`` together turn each row into
    the correct ``celery_app.send_task(name, ..., kwargs={...})`` call.
    Unmapped types are skipped silently — see the module docstring TODO.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STUCK_THRESHOLD_SEC)
    async with short_admin_session() as session:
        rows = list((await session.execute(
            select(Task).where(
                Task.status == "queued",
                Task.submitted_at < cutoff,
            )
        )).scalars().all())
        repo = TasksRepo(session)
        for row in rows:
            await repo.materialize_task(row)
    for row in rows:
        name = _TASK_TYPE_TO_CELERY_NAME.get(row.task_type)
        if name is None:
            # Unmapped task_type — sibling bug, out of scope; skip.
            continue
        payload = row.payload or {}
        kwargs = _build_kwargs_for(row.task_type, row, payload)
        celery_app.send_task(
            name,
            task_id=row.celery_id,
            kwargs=kwargs,
        )


# celery-beat schedule — picked up when the beat process boots with
# ``-A vibecanvas_api.celery_app``. Importing this module registers it
# on the global ``celery_app.conf`` (the celery_tasks package __init__
# imports this file, so the schedule is always present after app boot).
if not getattr(celery_app.conf, "beat_schedule", None):
    celery_app.conf.beat_schedule = {}
celery_app.conf.beat_schedule["phase6.reconciler"] = {
    "task": "phase6.reconciler.resubmit_stuck_queued",
    "schedule": RECONCILER_INTERVAL_SEC,
}
