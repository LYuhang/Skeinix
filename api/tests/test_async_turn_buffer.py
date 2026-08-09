"""AsyncTurnBuffer: replay, multi-sub, close, overflow."""

from __future__ import annotations

import asyncio
import pytest

from vibecanvas_api.streaming.async_turn_buffer import AsyncTurnBuffer


@pytest.mark.asyncio
async def test_single_subscriber_full_replay_then_close():
    buf = AsyncTurnBuffer()
    await buf.put({"i": 0})
    await buf.put({"i": 1})

    async def sub():
        out = []
        async for e in buf.subscribe():
            out.append(e)
        return out

    sub_task = asyncio.create_task(sub())
    await asyncio.sleep(0)  # let subscriber drain replay
    await buf.put({"i": 2})
    await buf.close()
    received = await sub_task
    assert received == [{"i": 0}, {"i": 1}, {"i": 2}]


@pytest.mark.asyncio
async def test_late_subscriber_gets_full_history():
    buf = AsyncTurnBuffer()
    await buf.put({"i": 0})
    await buf.put({"i": 1})
    await buf.close()

    async def sub():
        return [e async for e in buf.subscribe()]

    received = await sub()
    assert received == [{"i": 0}, {"i": 1}]


@pytest.mark.asyncio
async def test_two_subscribers_each_get_full_view():
    buf = AsyncTurnBuffer()
    await buf.put({"i": 0})

    async def sub():
        return [e async for e in buf.subscribe()]

    a = asyncio.create_task(sub())
    b = asyncio.create_task(sub())
    await asyncio.sleep(0)
    await buf.put({"i": 1})
    await buf.close()

    assert await a == [{"i": 0}, {"i": 1}]
    assert await b == [{"i": 0}, {"i": 1}]


@pytest.mark.asyncio
async def test_live_subscriber_does_not_miss_wake_between_batches():
    """A live subscriber must not depend on replay/refresh to see terminal frames.

    This exercises the producer/subscriber handoff where the consumer drains one
    batch, arms its waiter, then the producer publishes the next batch and closes.
    The subscriber must receive every event promptly without a heartbeat timeout.
    """
    buf = AsyncTurnBuffer()
    received: list[dict] = []

    async def sub():
        async for event in buf.subscribe():
            received.append(event)

    task = asyncio.create_task(sub())
    await asyncio.sleep(0)
    await buf.put({"i": 0})
    await asyncio.sleep(0)
    await buf.put({"i": 1})
    await buf.put({"i": 2})
    await buf.close()

    await asyncio.wait_for(task, timeout=1)
    assert received == [{"i": 0}, {"i": 1}, {"i": 2}]


@pytest.mark.asyncio
async def test_subscribe_with_ids_resumes_after_seq():
    buf = AsyncTurnBuffer()
    await buf.put(("CHAT_EVENT", {"i": 0}))
    await buf.put(("CHAT_EVENT", {"i": 1}))
    await buf.put(("CHAT_EVENT", {"i": 2}))
    await buf.close()

    received = [
        (seq, event)
        async for seq, event in buf.subscribe_with_ids(None, after_seq=1)
    ]

    assert received == [
        (2, ("CHAT_EVENT", {"i": 1})),
        (3, ("CHAT_EVENT", {"i": 2})),
    ]


@pytest.mark.asyncio
async def test_put_after_close_raises():
    buf = AsyncTurnBuffer()
    await buf.close()
    with pytest.raises(RuntimeError, match="closed"):
        await buf.put({"i": 0})


@pytest.mark.asyncio
async def test_overflow_protection():
    buf = AsyncTurnBuffer(max_size=2)
    await buf.put({"i": 0})
    await buf.put({"i": 1})
    with pytest.raises(RuntimeError, match="full"):
        await buf.put({"i": 2})


@pytest.mark.asyncio
async def test_drop_oldest_does_not_raise_on_overflow():
    """M1: exec buffers (drop_oldest=True) evict the oldest event at the
    cap instead of raising on a high-frame run."""
    buf = AsyncTurnBuffer(max_size=2, drop_oldest=True)
    await buf.put({"i": 0})
    await buf.put({"i": 1})
    # No raise — the oldest is evicted.
    await buf.put({"i": 2})
    assert buf.event_count == 2


@pytest.mark.asyncio
async def test_drop_oldest_late_subscriber_gets_retained_window_only():
    """A late subscriber after eviction sees the retained window (oldest
    frames are gone) but the cursor stays consistent — no skip, no dup."""
    buf = AsyncTurnBuffer(max_size=3, drop_oldest=True)
    for i in range(5):  # 0,1 evicted; 2,3,4 retained
        await buf.put({"i": i})
    await buf.close()

    received = [e async for e in buf.subscribe()]
    assert received == [{"i": 2}, {"i": 3}, {"i": 4}]


@pytest.mark.asyncio
async def test_drop_oldest_two_live_subscribers_consistent_across_eviction():
    """The absolute-cursor bookkeeping must stay correct across a
    front-eviction for EVERY subscriber: two subscribers, each draining the
    full closed stream, must see the same retained window with no skip and
    no duplicate — proving ``_dropped`` (the absolute base) is applied
    per-subscriber, not just for the first.

    Driven against a fully-populated then CLOSED buffer (each ``subscribe``
    drain is bounded — no consumer-loop/close-waker race), so the assertion
    is deterministic.
    """
    buf = AsyncTurnBuffer(max_size=3, drop_oldest=True)
    for i in range(6):  # 0,1,2 evicted; 3,4,5 retained (cap=3)
        await buf.put({"i": i})
    await buf.close()

    async def drain():
        return [e["i"] async for e in buf.subscribe()]

    a, b = await asyncio.gather(drain(), drain())
    # Both subscribers see exactly the retained window, in order, no dup.
    assert a == [3, 4, 5], f"subscriber A wrong window: {a}"
    assert b == [3, 4, 5], f"subscriber B wrong window: {b}"


@pytest.mark.asyncio
async def test_high_frame_exec_run_does_not_raise():
    """A simulated large loop (>10000 frames) on an exec buffer must not
    raise (the M1 false-failure)."""
    buf = AsyncTurnBuffer(drop_oldest=True)  # default cap 10000
    for i in range(25000):
        await buf.put({"i": i})  # would raise at 10000 without drop_oldest
    assert buf.event_count == 10000
