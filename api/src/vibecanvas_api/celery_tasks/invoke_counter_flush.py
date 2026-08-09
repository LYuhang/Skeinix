"""Spec §4.4 — flush Redis invoke counters into deployments.invoke_count.

Beat: every 60s. Best-effort: if Redis is empty or unreachable, no-op.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from vibecanvas_api.celery_app import celery_app
from vibecanvas_api.services.rate_limit import _get_redis
from vibecanvas_api.services.tenant_db import session_scope_admin


FLUSH_INTERVAL_SEC = 60.0


@celery_app.task(name="deployments.flush_invoke_counters")
def flush_invoke_counters():
    asyncio.run(_flush())


async def _flush() -> None:
    r = _get_redis()
    if r is None:
        return
    try:
        keys = await r.keys("dep:*:count")
    except Exception:
        return
    updates: list[tuple[str, int]] = []
    for k in keys:
        key_str = k.decode() if isinstance(k, bytes) else k
        try:
            dep_id = key_str.split(":")[1]
        except (IndexError, AttributeError):
            continue
        try:
            n = await r.getdel(key_str)
        except Exception:
            continue
        if n is not None:
            try:
                n_int = int(n.decode() if isinstance(n, bytes) else n)
            except (ValueError, AttributeError):
                continue
            if n_int > 0:
                updates.append((dep_id, n_int))
    if not updates:
        return
    async with session_scope_admin() as s:
        for dep_id, delta in updates:
            await s.execute(text(
                "UPDATE deployments SET invoke_count = invoke_count + :n, "
                "last_invoked_at = now() WHERE id = :id"
            ), {"id": dep_id, "n": delta})


if not getattr(celery_app.conf, "beat_schedule", None):
    celery_app.conf.beat_schedule = {}
celery_app.conf.beat_schedule["deployments.flush_invoke_counters"] = {
    "task": "deployments.flush_invoke_counters",
    "schedule": FLUSH_INTERVAL_SEC,
}
