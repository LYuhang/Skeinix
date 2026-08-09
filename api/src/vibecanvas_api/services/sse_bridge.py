"""Strictly ordered SSE for durable background tasks.

Two paths:
  1) Redis pubsub for the live tail (low-latency event delivery from worker).
  2) DB-polling SELECT-replay (gap-free; survives Redis outages).

Strategy:
  - Subscribe to Redis FIRST so any concurrently-published events buffer.
  - Then SELECT all rows from ``task_events`` with id > ``Last-Event-ID`` —
    emit those in id order.
  - Switch to live tail: alternate between (a) drain the Redis buffer
    (dedupe by id) and (b) periodically poll the DB for any rows the
    buffer missed (overflow safety).
  - Terminate when an event of type ``terminal`` is emitted. Business meaning
    such as ``task.finished`` or ``task.cancelled`` lives in payload.action.

If Redis is unavailable, fall back to pure DB polling — same ordering
guarantee via BIGSERIAL ``task_events.id``, just higher latency.

Tenant binding: ``tenant_id`` is bound at call time (route reads it from
the auth context) and used to open per-iteration ``session_scope``
sessions. Every DB read is RLS-bound to the same tenant the SSE consumer
authenticated as — no implicit elevation across the stream's lifetime.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque
from collections.abc import Awaitable, Callable

from vibecanvas_api.services.redis_channels import (
    current_authorization_generation,
    task_event_channel,
    task_event_envelope_matches,
)


BUFFER_CAP = 1000
POLL_INTERVAL_SEC = 0.2

TERMINAL_EVENT_TYPES = {"terminal"}


def _sse_event(id_: int, event_type: str, payload: dict) -> str:
    """Format one Server-Sent Event frame.

    ``id:`` lets the EventSource client resume via ``Last-Event-ID``;
    ``event:`` lets handlers route by type; ``data:`` is the JSON
    payload. Trailing blank line ends the frame per the SSE spec.
    """
    return (
        f"id: {id_}\n"
        f"event: {event_type}\n"
        f"data: {json.dumps(payload, default=str)}\n\n"
    )


def _payload_with_ts(payload: dict, ts) -> dict:
    out = dict(payload or {})
    if ts is not None:
        out["_event_ts"] = ts.isoformat()
    return out


async def _select_events_after(session, task_id: uuid.UUID, cursor: int) -> list:
    """Fetch all ``task_events`` rows with ``id > cursor`` for the task,
    ordered by id ASC (= chronological, since the column is BIGSERIAL)."""
    from vibecanvas_api.storage.repo_tasks import TasksRepo

    rows = await TasksRepo(session).events_for_task(
        task_id=task_id,
        after_seq=cursor,
        limit=BUFFER_CAP,
    )
    return [
        {
            "id": row.id,
            "event_type": row.event_type,
            "payload": row.payload,
            "ts": row.ts,
        }
        for row in rows
    ]


async def task_event_stream(
    *,
    task_id: uuid.UUID,
    last_event_id: int,
    tenant_id: str,
    redis_url: str | None,
    authorization_guard: Callable[[], Awaitable[bool]] | None = None,
    authorization_check_seconds: float = 5.0,
):
    """Async generator yielding SSE-formatted frames. Caller wraps in
    ``StreamingResponse(media_type='text/event-stream')``.

    The generator terminates on:
      * an emitted event whose ``event_type`` is in
        :data:`TERMINAL_EVENT_TYPES`, OR
      * upstream cancellation (``StreamingResponse`` cancels us when
        the client disconnects) — the ``finally`` block cleans up
        the Redis pubsub.
    """
    # Imported lazily so this module is importable in environments that
    # haven't initialised the engine yet (tests cherry-pick this module
    # without the FastAPI app's lifespan startup).
    from vibecanvas_api.storage.db import session_scope

    pubsub = None
    redis_client = None
    buffer: deque = deque(maxlen=BUFFER_CAP)
    overflowed = False
    drain_task: asyncio.Task | None = None
    authorization_generation = current_authorization_generation()
    channel = task_event_channel(
        tenant_id,
        task_id,
        authorization_generation=authorization_generation,
    )

    async def _drain_pubsub():
        """Background coroutine: pump pubsub messages into ``buffer``.
        Drops new arrivals when full (and sets ``overflowed`` so the
        main loop knows to fall back to a DB poll cycle)."""
        nonlocal overflowed
        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue
                try:
                    ev = json.loads(msg["data"])
                except Exception:
                    continue
                # Expected shape: {"id": int, "event_type": str, "payload": dict}
                if not isinstance(ev, dict) or "id" not in ev:
                    continue
                if not task_event_envelope_matches(
                    ev,
                    organization_id=tenant_id,
                    task_id=task_id,
                    authorization_generation=authorization_generation,
                ):
                    continue
                if len(buffer) >= BUFFER_CAP:
                    overflowed = True
                    continue
                buffer.append(ev)
        except asyncio.CancelledError:
            return
        except Exception:
            # Connection died — fallback path keeps going via DB polling.
            return

    # ----- Redis subscribe (best-effort). -----
    if redis_url:
        try:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(
                redis_url,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            pubsub = redis_client.pubsub()
            await pubsub.subscribe(channel)
            drain_task = asyncio.create_task(_drain_pubsub())
        except Exception:
            # Any failure here downgrades us to the DB-poll-only path.
            pubsub = None
            redis_client = None
            drain_task = None

    cursor = last_event_id
    next_authorization_check = 0.0

    async def _authorized() -> bool:
        nonlocal next_authorization_check
        if authorization_guard is None:
            return True
        now = asyncio.get_running_loop().time()
        if now < next_authorization_check:
            return True
        if not await authorization_guard():
            return False
        next_authorization_check = (
            now + max(1.0, authorization_check_seconds)
        )
        return True

    try:
        if not await _authorized():
            return
        # ----- SELECT-replay anything since `last_event_id`. -----
        async with session_scope(tenant_id=tenant_id) as session:
            rows = await _select_events_after(session, task_id, cursor)
        for row in rows:
            cursor = row["id"]
            yield _sse_event(row["id"], row["event_type"], _payload_with_ts(row["payload"], row["ts"]))
            if row["event_type"] in TERMINAL_EVENT_TYPES:
                return

        # ----- Drain anything buffered during the SELECT-replay window. -----
        for ev in list(buffer):
            if ev["id"] > cursor:
                cursor = ev["id"]
                yield _sse_event(ev["id"], ev["event_type"], ev["payload"])
                if ev["event_type"] in TERMINAL_EVENT_TYPES:
                    return
        buffer.clear()

        # ----- Live tail. -----
        while True:
            if not await _authorized():
                return
            # 1) Drain Redis buffer (cheap, low-latency path).
            while buffer:
                ev = buffer.popleft()
                if ev["id"] <= cursor:
                    continue
                cursor = ev["id"]
                yield _sse_event(ev["id"], ev["event_type"], ev["payload"])
                if ev["event_type"] in TERMINAL_EVENT_TYPES:
                    return

            # 2) Poll DB every tick to catch up. Redis is only a low-latency
            # accelerator; the task_events table is the authoritative stream.
            # This keeps the UI gap-free if Redis publish/subscription misses an
            # event after the initial replay.
            async with session_scope(tenant_id=tenant_id) as session:
                rows = await _select_events_after(session, task_id, cursor)
            if rows:
                for row in rows:
                    cursor = row["id"]
                    yield _sse_event(
                        row["id"], row["event_type"],
                        _payload_with_ts(row["payload"], row["ts"]),
                    )
                    if row["event_type"] in TERMINAL_EVENT_TYPES:
                        return
                overflowed = False
                continue
            overflowed = False

            # 3) Idle — sleep a beat before re-checking the buffer.
            await asyncio.sleep(POLL_INTERVAL_SEC)
    finally:
        if drain_task is not None:
            drain_task.cancel()
            try:
                await drain_task
            except (asyncio.CancelledError, Exception):
                pass
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:
                pass
        if redis_client is not None:
            try:
                await redis_client.aclose()
            except Exception:
                pass
