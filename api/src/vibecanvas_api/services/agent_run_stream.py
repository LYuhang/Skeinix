"""Database-backed replay/live-tail SSE for interactive agent runs."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import time

import structlog
from vibecanvas_api.streaming.sse import format_event


POLL_INTERVAL_SECONDS = 0.25
TERMINAL_STATUSES = {"completed", "cancelled", "failed"}
logger = structlog.get_logger(__name__)


async def agent_run_event_stream(
    *,
    run_id: str,
    after_seq: int,
    tenant_id: str,
    authorization_guard: Callable[[], Awaitable[bool]] | None = None,
    authorization_check_seconds: float = 5.0,
):
    from vibecanvas_api.storage.agent_runs_repo import AgentRunsRepo
    from vibecanvas_api.storage.db import session_scope

    cursor = max(0, int(after_seq or 0))
    idle_ticks = 0
    delivered = 0
    started_at = time.perf_counter()
    next_authorization_check = 0.0
    logger.info(
        "agent_sse_replay",
        phase="attached",
        run_id=run_id,
        after_seq=cursor,
    )
    try:
        while True:
            now = time.monotonic()
            if (
                authorization_guard is not None
                and now >= next_authorization_check
            ):
                if not await authorization_guard():
                    logger.info(
                        "agent_sse_authorization_lease_closed",
                        run_id=run_id,
                    )
                    return
                next_authorization_check = (
                    now + max(1.0, authorization_check_seconds)
                )
            async with session_scope(tenant_id=tenant_id) as session:
                repo = AgentRunsRepo(session)
                run = await repo.get(run_id)
                if run is None:
                    return
                events = await repo.list_events(run_id, cursor)
                status = run.status

            expected = cursor + 1
            for event in events:
                if event.seq != expected:
                    logger.error(
                        "agent_sse_sequence_gap",
                        run_id=run_id,
                        expected_seq=expected,
                        actual_seq=event.seq,
                        after_seq=cursor,
                    )
                    # Never silently stream an incomplete transcript. Closing
                    # makes the client retain its last acknowledged cursor and
                    # retry the durable replay instead of applying later frames.
                    raise RuntimeError(
                        f"durable Agent event gap: expected {expected}, got {event.seq}"
                    )
                cursor = event.seq
                expected += 1
                delivered += 1
                yield format_event(event.event_type, event.payload, event_id=event.seq)

            if status in TERMINAL_STATUSES and not events:
                return

            idle_ticks += 1
            if idle_ticks >= 60:  # transport heartbeat about every 15 seconds
                idle_ticks = 0
                yield format_event("HEARTBEAT", {}, event_id=None)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    finally:
        logger.info(
            "agent_sse_replay",
            phase="detached",
            run_id=run_id,
            last_seq=cursor,
            event_count=delivered,
            elapsed_ms=int((time.perf_counter() - started_at) * 1000),
        )
