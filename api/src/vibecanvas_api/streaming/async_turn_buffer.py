"""asyncio.Queue-backed turn buffer with multi-subscriber full-replay.

Sibling to legacy storage/agent_turn_buffer.py (multiprocessing.Queue-
backed), which is preserved for compatibility. Semantics:

- producer calls put(event)
- multiple subscribers (typically: the original POST SSE stream +
  later GET resume streams) call subscribe() and each gets:
    1. full replay of previously-buffered events (from start)
    2. future events as they arrive
- close() marks the stream complete; subscribers iterating drain the
  remaining replay then exit.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, AsyncIterator


class AsyncTurnBuffer:
    """In-memory replay buffer for SSE events of one agent/exec turn.

    Two overflow disciplines apply at the ``max_size`` cap:

    * ``drop_oldest=False`` (DEFAULT — the CHAT path): on overflow ``put``
      RAISES. Chat turns are resumable (GET-resume re-subscribes and
      replays the full event list with a Last-Event-ID guarantee), so
      silently dropping the head of a chat turn would corrupt resume. A
      runaway chat turn is a bug we want surfaced, not hidden.

    * ``drop_oldest=True`` (the EXEC path): on overflow the OLDEST event is
      evicted (ring buffer) instead of raising. A large loop
      (LoopBegin/LoopEnd × thousands) now emits 2 frames/node/iteration
      (``running`` + ``success``), which would blow past the 10000 cap and
      falsely fail the run. Execution is NOT resumable like chat — the
      canvas/Execution-tab view is "latest state per node," not a faithful
      replay of every loop iteration — so dropping the oldest frames is
      acceptable: a late subscriber simply misses the early-iteration
      frames but still sees the terminal frame and the live tail. The
      absolute index space is preserved via ``_dropped`` so every
      subscriber's cursor stays correct after a front-eviction.
    """

    def __init__(self, max_size: int = 10000,
                 *, drop_oldest: bool = False) -> None:
        self._events: list[tuple[int, Any]] = []
        self._closed = False
        self._condition = asyncio.Condition()
        self._max_size = max_size  # protects against runaway turns
        self._drop_oldest = drop_oldest
        self._next_seq = 1

    async def put(self, event: Any) -> int:
        return await self.put_after_prepare(event)

    async def put_after_prepare(
        self,
        event: Any,
        prepare: Callable[[int], Awaitable[None]] | None = None,
    ) -> int:
        """Persist/prepare an event before making it visible to subscribers.

        The sequence is reserved under the same condition lock used to publish
        the event. If ``prepare`` fails, nothing is appended or notified and the
        sequence remains available for the caller's terminal error event.
        """
        async with self._condition:
            if self._closed:
                raise RuntimeError("put() on closed buffer")
            if len(self._events) >= self._max_size:
                if self._drop_oldest:
                    self._events.pop(0)
                else:
                    raise RuntimeError(
                        f"buffer full ({self._max_size} events); "
                        "refuse to grow"
                    )
            seq = self._next_seq
            if prepare is not None:
                await prepare(seq)
            self._next_seq += 1
            self._events.append((seq, event))
            self._condition.notify_all()
            return seq

    async def close(self) -> None:
        async with self._condition:
            self._closed = True
            self._condition.notify_all()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def event_count(self) -> int:
        return len(self._events)

    async def subscribe(self) -> AsyncIterator[Any]:
        """Yield every event from start, then wait for new ones until close."""
        async for event in self.subscribe_with_heartbeat(None, after_seq=0):
            yield event

    async def subscribe_with_heartbeat(
        self,
        heartbeat_seconds: float | None = 15.0,
        *,
        after_seq: int = 0,
    ) -> AsyncIterator[Any]:
        """Yield buffered events and optional heartbeat sentinels while idle.

        Chat turns can spend a long time inside one tool before the producer has
        another semantic frame to publish. Heartbeats keep the HTTP/SSE transport
        active without mutating the replay buffer, so reconnect/resume still
        replays only real turn events.
        """
        async for _seq, event in self.subscribe_with_ids(
            heartbeat_seconds, after_seq=after_seq,
        ):
            yield event

    async def subscribe_with_ids(
        self,
        heartbeat_seconds: float | None = 15.0,
        *,
        after_seq: int = 0,
    ) -> AsyncIterator[tuple[int | None, Any]]:
        """Yield ``(seq, event)`` pairs, plus ``(None, HEARTBEAT)`` while idle.

        Uses the canonical condition-variable pattern: while no event with
        ``seq > cursor`` exists and the buffer is open, wait on the same lock
        that producers use to append + notify. This gives queue-like delivery
        semantics for every live subscriber while retaining replay for resumes.
        """
        cursor = max(0, int(after_seq or 0))
        while True:
            async with self._condition:
                while True:
                    pending = [(seq, ev) for seq, ev in self._events if seq > cursor]
                    if pending or self._closed:
                        break
                    if heartbeat_seconds is None:
                        await self._condition.wait()
                    else:
                        try:
                            await asyncio.wait_for(
                                self._condition.wait(),
                                timeout=heartbeat_seconds,
                            )
                        except asyncio.TimeoutError:
                            pending = [(None, ("HEARTBEAT", {}))]
                            break
                closed_now = self._closed
            for seq, event in pending:
                if seq is not None:
                    cursor = seq
                yield seq, event
            if closed_now:
                return
