"""Periodic consumer for durable account erasure jobs."""
from __future__ import annotations

import asyncio

from vibecanvas_api.celery_app import celery_app
from vibecanvas_api.config import config
from vibecanvas_api.security.purge import run_one_due_purge


@celery_app.task(name="data_purge.run_due", bind=True, acks_late=True)
def run_due(self) -> dict[str, object]:
    del self
    if not config.purge_worker_enabled:
        return {"status": "disabled", "processed": False}
    processed = asyncio.run(run_one_due_purge())
    return {"status": "ok", "processed": processed}


if not getattr(celery_app.conf, "beat_schedule", None):
    celery_app.conf.beat_schedule = {}
celery_app.conf.beat_schedule["data_purge.run_due"] = {
    "task": "data_purge.run_due",
    "schedule": 60.0,
    "options": {"queue": "maintenance"},
}
