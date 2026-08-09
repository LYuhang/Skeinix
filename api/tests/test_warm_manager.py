"""WarmPoolManager — per-tenant lazy-start + idle-reap + global cap + LRU
(RE-6 Warm-prod T3).

gVisor-free: a ``FakePool`` (whose ``submit`` blocks on a test-controlled
``threading.Event`` to model a "busy" pool, and records start/stop) is injected
via ``pool_factory``; a mutable ``FakeClock`` is injected via ``clock`` so the
reaper/LRU logic is tested deterministically (no real time / no sleeps).

The two hard properties under test (binding corrections B1/B2):
- **B1:** ``reap``/LRU-evict NEVER touch an in-flight pool (``inflight > 0``) —
  a long-running job must not be killed just because its ``last_used`` is old.
  ``last_used`` is refreshed on COMPLETION, not only on start.
- **B2:** ``inflight += 1`` happens INSIDE the same locked critical section as
  get-or-lazy-start, BEFORE the lock is released for the ``pool.submit`` wait —
  so the pool is un-evictable during its run, and two concurrent first-submits
  for a new tenant start exactly ONE pool (double-checked lazy-start).
"""

from __future__ import annotations

import threading

import pytest

from vibecanvas_api.services.sandbox import (
    PoolCapacityExceeded,
    WarmPoolManager,
)


class FakeClock:
    """A mutable monotonic clock the test advances by hand."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class FakePool:
    """Stand-in for ``WarmGvisorPool``. ``submit`` blocks on ``release`` (an
    Event) to model "busy"; records start/stop/submit counts + the ctor kwargs.

    By default ``release`` is set → ``submit`` returns immediately. A test that
    wants a pool to hold a job "in-flight" registers the tenant as blocking in
    the factory (see ``_make_manager(blocking=...)``), then ``submit`` waits on
    ``release`` until the test sets it.
    """

    def __init__(self, *, blocking=False, **kwargs) -> None:
        self.kwargs = kwargs
        self.tenant = kwargs.get("tenant")
        self.started = 0
        self.stopped = 0
        self.submits = 0
        self.release = threading.Event()
        if not blocking:
            self.release.set()
        # Signalled once a submit body has ENTERED (so the test can wait until a
        # submit is genuinely in-flight before advancing the clock / reaping).
        self.entered = threading.Event()
        self.egress_leases = []
        self.egress_releases = []

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def acquire_egress_hosts(self, hosts):
        lease_id = f"lease-{len(self.egress_leases) + 1}"
        self.egress_leases.append((lease_id, set(hosts)))
        return lease_id

    def release_egress_hosts(self, lease_id):
        self.egress_releases.append(lease_id)

    def submit(self, *, workflow, inputs, run_id, tenant, timeout):
        self.submits += 1
        self.entered.set()
        self.release.wait()  # block while "busy" if release is clear
        return ("result", self.tenant, run_id)

    async def submit_stream(self, *, workflow, inputs, run_id, tenant, timeout):
        """Streaming sibling: yields a node_event, then (if released) a result.
        A test that wants to ABANDON mid-stream leaves ``release`` clear so the
        generator parks after the first frame — then closes it to fire the
        manager's inflight finally."""
        import asyncio as _aio
        self.submits += 1
        self.entered.set()
        yield {"type": "node_event", "status": "success", "node_id": "node_1",
               "run_id": run_id}
        while not self.release.is_set():
            await _aio.sleep(0.005)
        yield {"type": "result", "final_outputs": {}, "error_dict": {},
               "execution_time": 0.1}

    def cancel(self, *, tenant, run_id):
        self.cancelled = getattr(self, "cancelled", [])
        self.cancelled.append((tenant, run_id))


def _make_manager(clock, *, max_workers=4, idle_timeout=300.0, blocking=()):
    """Build a manager with a recording factory. ``blocking`` is a set of
    tenants whose pools start in the blocked state (submit hangs on release)."""
    created: list[FakePool] = []
    blocking_set = set(blocking)

    def factory(**kwargs):
        p = FakePool(blocking=kwargs.get("tenant") in blocking_set, **kwargs)
        created.append(p)
        return p

    mgr = WarmPoolManager(
        provider=object(),
        store_root="/tmp/objects",
        work_root_base="/tmp/work",
        max_workers=max_workers,
        idle_timeout=idle_timeout,
        pool_factory=factory,
        clock=clock,
    )
    return mgr, created


def _submit(mgr, tenant, run_id="r1", timeout=60.0):
    return mgr.submit(
        tenant=tenant,
        workflow={"node_1": {"node_type": "StartNode"}},
        inputs={},
        run_id=run_id,
        timeout=timeout,
    )


def _pool_for(created, tenant):
    for p in created:
        if p.tenant == tenant:
            return p
    raise AssertionError(f"no pool created for {tenant}")


def _submit_inflight(mgr, created, tenant, run_id="r1"):
    """Start a submit for ``tenant`` in a background thread and block until it
    is genuinely in-flight (inside ``pool.submit``, inflight already incremented
    under the lock). Returns (thread, pool). The pool's tenant must be in the
    manager's ``blocking`` set so it hangs on ``release``."""
    t = threading.Thread(target=lambda: _submit(mgr, tenant, run_id), daemon=True)
    t.start()
    # Wait for the pool to exist + the submit to enter.
    pool = None
    for _ in range(1000):
        try:
            pool = _pool_for(created, tenant)
        except AssertionError:
            pass
        if pool is not None and pool.entered.wait(timeout=0.01):
            break
    assert pool is not None and pool.entered.is_set()
    return t, pool


# --------------------------------------------------------------------------
# lazy-start + reuse
# --------------------------------------------------------------------------
def test_lazy_start_one_pool_per_tenant():
    clock = FakeClock()
    mgr, created = _make_manager(clock)

    _submit(mgr, "A")
    assert len(created) == 1
    assert created[0].started == 1
    assert created[0].tenant == "A"


def test_second_submit_reuses_pool():
    clock = FakeClock()
    mgr, created = _make_manager(clock)

    _submit(mgr, "A", run_id="r1")
    _submit(mgr, "A", run_id="r2")
    assert len(created) == 1  # reused
    assert created[0].started == 1  # started once
    assert created[0].submits == 2


def test_work_root_is_per_tenant():
    # N5: the /work job channel is the SECOND isolation leg — disjoint per tenant.
    clock = FakeClock()
    mgr, created = _make_manager(clock)
    _submit(mgr, "A")
    _submit(mgr, "B")
    roots = {p.kwargs["work_root"] for p in created}
    assert roots == {"/tmp/work/A", "/tmp/work/B"}


def test_submit_returns_pool_result():
    clock = FakeClock()
    mgr, _ = _make_manager(clock)
    assert _submit(mgr, "A", run_id="rX") == ("result", "A", "rX")


# --------------------------------------------------------------------------
# B1 — reap skips in-flight; reaps idle
# --------------------------------------------------------------------------
def test_reap_idle_pool_after_timeout():
    clock = FakeClock()
    mgr, created = _make_manager(clock, idle_timeout=300.0)
    _submit(mgr, "A")
    clock.advance(301.0)
    reaped = mgr.reap()
    assert reaped == ["A"]
    assert created[0].stopped == 1


def test_reap_keeps_recently_used_pool():
    clock = FakeClock()
    mgr, created = _make_manager(clock, idle_timeout=300.0)
    _submit(mgr, "A")
    clock.advance(100.0)  # within idle_timeout
    reaped = mgr.reap()
    assert reaped == []
    assert created[0].stopped == 0


def test_reap_NEVER_reaps_inflight_pool():
    """B1: a pool with inflight>0 (a long-running job) must NOT be reaped even
    when its last_used is far past idle_timeout; once the job completes and the
    clock advances, the now-idle pool IS reaped."""
    clock = FakeClock()
    mgr, created = _make_manager(clock, idle_timeout=300.0, blocking={"A"})

    t, pool = _submit_inflight(mgr, created, "A")

    # The job is in-flight. Advance the clock well past idle_timeout.
    clock.advance(10_000.0)
    assert mgr.reap() == []  # in-flight → NOT reaped
    assert pool.stopped == 0

    # Complete the job, let the submit return + refresh last_used on completion.
    pool.release.set()
    t.join(timeout=5.0)
    assert not t.is_alive()

    # Still recent right after completion (last_used refreshed to now).
    assert mgr.reap() == []
    # Now advance past idle_timeout → reaped.
    clock.advance(301.0)
    assert mgr.reap() == ["A"]
    assert pool.stopped == 1


# --------------------------------------------------------------------------
# B2 / global cap + LRU
# --------------------------------------------------------------------------
def test_cap_evicts_lru_idle_pool():
    """max_workers=2: A then B fill the cap; a third tenant C evicts the LRU
    idle pool (oldest last_used, inflight==0) and starts C."""
    clock = FakeClock()
    mgr, created = _make_manager(clock, max_workers=2)

    clock.advance(1.0)
    _submit(mgr, "A")  # A last_used = 1001
    clock.advance(1.0)
    _submit(mgr, "B")  # B last_used = 1002
    clock.advance(1.0)
    _submit(mgr, "C")  # cap=2 → evict A (oldest), start C

    pool_a = _pool_for(created, "A")
    pool_b = _pool_for(created, "B")
    pool_c = _pool_for(created, "C")
    assert pool_a.stopped == 1  # LRU evicted
    assert pool_b.stopped == 0  # kept
    assert pool_c.started == 1  # new pool started
    # A dropped from the map; resubmitting A starts a fresh pool.
    assert "A" not in mgr._pools and "B" in mgr._pools and "C" in mgr._pools


def test_cap_all_busy_raises():
    """B2: max_workers=2 with A AND B both BUSY (inflight>0) → a third tenant C
    finds no evictable (inflight==0) pool → PoolCapacityExceeded."""
    clock = FakeClock()
    mgr, created = _make_manager(clock, max_workers=2, blocking={"A", "B"})

    ta, _ = _submit_inflight(mgr, created, "A")
    tb, _ = _submit_inflight(mgr, created, "B")

    with pytest.raises(PoolCapacityExceeded):
        _submit(mgr, "C")

    # Cleanup: release the busy pools so threads exit.
    for p in created:
        p.release.set()
    ta.join(timeout=5.0)
    tb.join(timeout=5.0)


def test_evict_skips_busy_even_if_older():
    """B2/B1: A is BUSY (inflight>0) but STALE (oldest last_used); B is idle but
    recent. At cap=2, a third tenant C evicts B (idle), NOT A (busy) — even
    though A is "older"."""
    clock = FakeClock()
    mgr, created = _make_manager(clock, max_workers=2, blocking={"A"})

    # A starts busy and stale.
    ta, pool_a = _submit_inflight(mgr, created, "A")
    clock.advance(1000.0)  # make A's last_used the oldest
    _submit(mgr, "B")  # B idle, recent
    clock.advance(1.0)
    _submit(mgr, "C")  # cap=2 → must evict B (idle), not A (busy)

    pool_b = _pool_for(created, "B")
    pool_c = _pool_for(created, "C")
    assert pool_a.stopped == 0  # busy → NOT evicted despite being oldest
    assert pool_b.stopped == 1  # idle → evicted
    assert pool_c.started == 1
    assert "A" in mgr._pools and "B" not in mgr._pools and "C" in mgr._pools

    pool_a.release.set()
    ta.join(timeout=5.0)


# --------------------------------------------------------------------------
# B2 — double-checked lazy-start under concurrency
# --------------------------------------------------------------------------
def test_concurrent_first_submit_starts_one_pool():
    """B2: two threads submit(tenant=A) concurrently → exactly ONE pool.start().
    A barrier maximizes the race window."""
    clock = FakeClock()
    mgr, created = _make_manager(clock)

    barrier = threading.Barrier(2)

    def worker(run_id):
        barrier.wait()
        _submit(mgr, "A", run_id=run_id)

    threads = [threading.Thread(target=worker, args=(f"r{i}",)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
        assert not t.is_alive()

    a_pools = [p for p in created if p.tenant == "A"]
    assert len(a_pools) == 1
    assert a_pools[0].started == 1
    assert a_pools[0].submits == 2


# --------------------------------------------------------------------------
# shutdown
# --------------------------------------------------------------------------
def test_shutdown_stops_all_pools():
    clock = FakeClock()
    mgr, created = _make_manager(clock)
    _submit(mgr, "A")
    _submit(mgr, "B")
    mgr.shutdown()
    assert all(p.stopped == 1 for p in created)
    assert mgr._pools == {}


# --------------------------------------------------------------------------
# STEP 3 — submit_stream inflight discipline (B2 — no leak on abandon)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_submit_stream_happy_path_inflight_returns_to_zero():
    clock = FakeClock()
    mgr, created = _make_manager(clock)

    out = []
    async for item in mgr.submit_stream(
        tenant="A", workflow={}, inputs={}, run_id="r1", timeout=10.0
    ):
        out.append(item)

    assert [m["type"] for m in out] == ["node_event", "result"]
    assert mgr._pools["A"].inflight == 0  # released on completion
    assert created[0].submits == 1


@pytest.mark.asyncio
async def test_submit_stream_inflight_released_on_abandon():
    """B2 CRUX: a consumer abandons the stream mid-way (aclose before terminal) →
    the manager's ``finally`` runs → inflight returns to 0 so reap/evict are not
    permanently blocked on a zombie pool."""
    clock = FakeClock()
    # ``blocking={"A"}`` → the FakePool's submit_stream parks after the first
    # node_event (release stays clear), modelling a still-running sandbox run.
    mgr, created = _make_manager(clock, blocking={"A"})

    agen = mgr.submit_stream(
        tenant="A", workflow={}, inputs={}, run_id="r1", timeout=10.0
    )
    first = await agen.__anext__()
    assert first["type"] == "node_event"
    # The pool is in-flight while the stream is live.
    assert mgr._pools["A"].inflight == 1
    # Abandon WITHOUT consuming the terminal frame.
    await agen.aclose()
    # inflight released → reap can now reclaim the idle pool.
    assert mgr._pools["A"].inflight == 0
    clock.advance(10_000.0)
    assert mgr.reap() == ["A"]


@pytest.mark.asyncio
async def test_submit_stream_inflight_released_on_guard_raise():
    """A pre-yield guard raise (host-only node etc.) inside pool.submit_stream
    surfaces on the first __anext__ → the manager finally still drops inflight."""
    clock = FakeClock()
    mgr, created = _make_manager(clock)

    class _RaisingPool(FakePool):
        async def submit_stream(self, **kwargs):
            raise RuntimeError("host-only node")
            yield  # pragma: no cover — make it a generator

    def factory(**kwargs):
        p = _RaisingPool(**kwargs)
        created.append(p)
        return p

    mgr._pool_factory = factory
    agen = mgr.submit_stream(
        tenant="A", workflow={}, inputs={}, run_id="r1", timeout=10.0
    )
    with pytest.raises(RuntimeError, match="host-only"):
        await agen.__anext__()
    assert mgr._pools["A"].inflight == 0


@pytest.mark.asyncio
async def test_manager_cancel_delegates_to_pool():
    clock = FakeClock()
    mgr, created = _make_manager(clock)
    _submit(mgr, "A")  # lazy-start a pool
    mgr.cancel(tenant="A", run_id="r1")
    assert _pool_for(created, "A").cancelled == [("A", "r1")]


def test_manager_cancel_unknown_tenant_is_noop():
    clock = FakeClock()
    mgr, _ = _make_manager(clock)
    mgr.cancel(tenant="ghost", run_id="r1")  # no pool → no error


# --------------------------------------------------------------------------
# task #483 — HARD cancel: inflight stays correct when a kill ends the stream
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cancel_midstream_inflight_returns_to_zero():
    """task #483: a streaming run is hard-cancelled mid-stream → the pool kill
    makes ``submit_stream`` terminate → the manager generator's ``finally`` runs
    EXACTLY once → inflight returns to 0 (no leak, no double-decrement), so
    reap/evict are not blocked."""
    import asyncio as _aio

    clock = FakeClock()

    class _KillEndsStreamPool(FakePool):
        """Models the real warm pool: ``cancel`` kills+restarts the worker, which
        makes ``submit_stream`` observe the dead worker and end. Here ``cancel``
        sets ``release`` (the run was "killed" → the parked generator unblocks and
        terminates)."""

        async def submit_stream(self, *, workflow, inputs, run_id, tenant, timeout):
            self.submits += 1
            self.entered.set()
            yield {"type": "node_event", "status": "success",
                   "node_id": "node_1", "run_id": run_id}
            # Park (mid-node runaway) until cancel "kills" the worker.
            while not self.release.is_set():
                await _aio.sleep(0.005)
            # After a kill the stream ends WITHOUT a clean result (the warm pool
            # yields a terminal then returns; here we just end the generator).

        def cancel(self, *, tenant, run_id):
            self.cancelled = getattr(self, "cancelled", [])
            self.cancelled.append((tenant, run_id))
            self.release.set()  # the kill unblocks the parked stream

    created: list = []

    def factory(**kwargs):
        p = _KillEndsStreamPool(blocking=True, **kwargs)
        created.append(p)
        return p

    mgr = WarmPoolManager(
        provider=object(), store_root="/s", work_root_base="/w",
        pool_factory=factory, clock=clock,
    )

    agen = mgr.submit_stream(
        tenant="A", workflow={}, inputs={}, run_id="r1", timeout=10.0
    )
    first = await agen.__anext__()
    assert first["type"] == "node_event"
    assert mgr._pools["A"].inflight == 1  # in-flight while streaming

    # Hard cancel routes to pool.cancel (marker + kill); the kill ends the stream.
    mgr.cancel(tenant="A", run_id="r1")
    assert created[0].cancelled == [("A", "r1")]

    # Drain the now-terminating stream (consumer finishes the async-for).
    async for _ in agen:
        pass

    # inflight back to 0 — released EXACTLY once by the generator's finally.
    assert mgr._pools["A"].inflight == 0
    # reap can now reclaim the idle pool (not blocked).
    clock.advance(10_000.0)
    assert mgr.reap() == ["A"]
