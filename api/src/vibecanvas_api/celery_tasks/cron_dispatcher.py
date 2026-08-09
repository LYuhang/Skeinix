"""Deployments §6.5 — cron dispatcher.

Beat runs every 60s. For each enabled cron deployment whose next fire
is past, we (1) CAS-guard so concurrent beat workers don't double-fire,
(2) send_task to the deployment_invoke worker.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from sqlalchemy import text

from vibecanvas_api.celery_app import celery_app
from vibecanvas_api.services.queue_routing import route_for
from vibecanvas_api.services.tenant_db import session_scope_admin


CRON_DISPATCHER_INTERVAL_SEC = 60.0


@celery_app.task(name="deployments.cron_dispatcher")
def dispatch_due_crons():
    asyncio.run(_dispatch())


async def _dispatch() -> None:
    async with session_scope_admin() as s:
        rows = (await s.execute(text(
            "SELECT id, tenant_id, user_id, wf_id, cron_expr, cron_tz, "
            "       last_fire_at, created_at "
            "FROM deployments WHERE trigger_type='cron' AND enabled=TRUE "
            "AND deleted_at IS NULL"
        ))).mappings().all()

    now = datetime.now(timezone.utc)
    for r in rows:
        anchor = r["last_fire_at"] or r["created_at"]
        if anchor is None:
            continue
        try:
            tz = ZoneInfo(r["cron_tz"] or "UTC")
            local_anchor = anchor.astimezone(tz)
            next_fire_local = croniter(
                r["cron_expr"], start_time=local_anchor,
            ).get_next(ret_type=datetime)
            if next_fire_local.tzinfo is None:
                next_fire_local = next_fire_local.replace(tzinfo=tz)
            next_fire_utc = next_fire_local.astimezone(timezone.utc)
        except Exception:
            continue

        if next_fire_utc > now:
            continue

        if not await _attempt_fire(r["id"], next_fire_utc):
            continue

        task_uuid = uuid.uuid4()
        celery_app.send_task(
            "deployment_invoke",
            task_id=str(task_uuid),
            queue=route_for("deployment_invoke", r["id"]),
            kwargs=dict(
                task_id=str(task_uuid),
                tenant_id=str(r["tenant_id"]),
                deployment_id=str(r["id"]),
                inputs={},
            ),
        )


async def _attempt_fire(deployment_id: uuid.UUID, next_fire: datetime) -> bool:
    """Atomic CAS — returns True iff this caller wins the race."""
    async with session_scope_admin() as s:
        row = (await s.execute(text(
            "UPDATE deployments SET last_fire_at = :nf "
            "WHERE id = :id AND deleted_at IS NULL "
            "AND (last_fire_at IS NULL OR last_fire_at < :nf) "
            "RETURNING id"
        ), {"id": deployment_id, "nf": next_fire})).fetchone()
        await s.commit()
    return row is not None


if not getattr(celery_app.conf, "beat_schedule", None):
    celery_app.conf.beat_schedule = {}
celery_app.conf.beat_schedule["deployments.cron_dispatcher"] = {
    "task": "deployments.cron_dispatcher",
    "schedule": CRON_DISPATCHER_INTERVAL_SEC,
}
