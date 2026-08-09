"""WarmPoolManager — a per-tenant fleet of ``WarmGvisorPool``s with lazy-start,
idle-reap, a global worker cap, and LRU eviction (RE-6 Warm-prod T3).

This is the LIFECYCLE layer on top of T2's per-tenant ``WarmGvisorPool``. Each
tenant gets its OWN long-lived pool (its OWN ``work_root={base}/{tenant}`` job
channel — N5: the second isolation leg, a job for A is never visible to B's
worker), started lazily on the tenant's first submit. Pools that go idle past
``idle_timeout`` are reaped; a global ``max_workers`` cap bounds the fleet
(1 pool == 1 worker today — N6: count-only, no per-worker memory cap until P3
cgroups), evicting the least-recently-used IDLE pool when a new tenant would
exceed it.

The CONCURRENCY model (binding corrections B1/B2) is the crux:

- **B1 — never kill a running job.** Each ``_Entry`` carries an ``inflight``
  refcount. ``reap`` and LRU-eviction SKIP any pool with ``inflight > 0`` — a
  long run's ``last_used`` is OLD precisely *because the run is long*, so a
  ``last_used``-only reaper would SIGKILL the worker mid-run. ``last_used`` is
  refreshed on submit COMPLETION (not only at start), so a just-finished pool is
  treated as recently used.

- **B2 — inflight++ inside the lock.** ``submit`` does get-or-lazy-start AND
  ``inflight += 1`` inside ONE atomic critical section, THEN releases the lock
  for the (blocking) ``pool.submit`` wait. This closes the evict-races-submit
  TOCTOU (a concurrent evict can't ``stop()`` a pool that already has
  ``inflight > 0``) and makes lazy-start double-checked (two concurrent
  first-submits for a new tenant start exactly one pool).

NOT wired as a default execution path — capability only.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from .warm import WarmGvisorPool


@dataclass
class _Entry:
    """Per-tenant fleet slot: the live pool, its last-used wall (refreshed on
    submit START and COMPLETION — B1), and its in-flight job refcount (the
    un-evictable / un-reapable guard — B1/B2)."""

    pool: object
    last_used: float
    inflight: int = 0


class PoolCapacityExceeded(Exception):
    """Raised when a new tenant would exceed ``max_workers`` and EVERY existing
    pool is in-flight (``inflight > 0``) — there is no idle pool to LRU-evict, so
    the manager refuses rather than evict a running job (B1/B2)."""


class WarmPoolManager:
    """A fleet of per-tenant ``WarmGvisorPool``s with lazy/idle/cap/LRU lifecycle."""

    def __init__(
        self,
        *,
        provider,
        store_root: str,
        work_root_base: str,
        max_workers: int = 4,
        idle_timeout: float = 300.0,
        pool_factory=WarmGvisorPool,
        clock=time.monotonic,
    ) -> None:
        self.provider = provider
        self.store_root = store_root
        self.work_root_base = work_root_base
        self.max_workers = max_workers
        self.idle_timeout = idle_timeout
        # ``pool_factory`` + ``clock`` are injectable so tests drive a fake pool
        # (whose submit blocks on an Event) + a hand-advanced clock — fully
        # deterministic, gVisor-free.
        self._pool_factory = pool_factory
        self._clock = clock
        self._pools: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._reaper: "threading.Thread | None" = None
        self._reaper_stop = threading.Event()

    # -- submit ------------------------------------------------------------
    def submit(
        self,
        *,
        tenant,
        workflow,
        inputs,
        run_id,
        timeout: float = 60.0,
        allow_hosts=(),
    ):
        """Run one workflow on ``tenant``'s warm pool (lazy-starting it, possibly
        LRU-evicting an idle pool to stay under ``max_workers``).

        B2: get-or-lazy-start + ``inflight += 1`` happen in ONE locked critical
        section, BEFORE the blocking ``pool.submit`` wait — so the pool can't be
        evicted/reaped out from under a run, and concurrent first-submits for a
        new tenant start exactly one pool. The ``finally`` refreshes ``last_used``
        on COMPLETION (B1) and drops the refcount.
        """
        with self._lock:
            entry = self._pools.get(tenant)
            if entry is None:
                # Lazy-start. Make room first if a NEW pool would exceed the cap
                # (1 pool == 1 worker — N6). Double-checked: another thread that
                # raced us here would have populated ``_pools[tenant]`` and we'd
                # have taken the reuse branch above.
                if len(self._pools) >= self.max_workers:
                    self._evict_lru_idle_locked()  # may raise PoolCapacityExceeded
                pool = self._pool_factory(
                    provider=self.provider,
                    store_root=self.store_root,
                    work_root=f"{self.work_root_base}/{tenant}",
                    tenant=tenant,
                )
                pool.start()
                entry = _Entry(pool=pool, last_used=self._clock())
                self._pools[tenant] = entry
            # inflight++ INSIDE the lock (B2): the pool is now un-evictable.
            entry.inflight += 1
            entry.last_used = self._clock()

        lease_id = entry.pool.acquire_egress_hosts(allow_hosts)
        try:
            return entry.pool.submit(
                workflow=workflow,
                inputs=inputs,
                run_id=run_id,
                tenant=tenant,
                timeout=timeout,
            )
        finally:
            entry.pool.release_egress_hosts(lease_id)
            with self._lock:
                entry.inflight -= 1
                entry.last_used = self._clock()  # refresh on COMPLETION (B1)

    async def submit_stream(
        self,
        *,
        tenant,
        workflow,
        inputs,
        run_id,
        timeout: float = 120.0,
        allow_hosts=(),
    ):
        """STREAMING sibling of :meth:`submit` (RE-6 debug-execute). Drives
        ``pool.submit_stream`` and re-yields each item (``node_event`` / ``result``
        / ``timeout``), with the SAME inflight discipline as :meth:`submit`.

        B2 — the crux: ``inflight += 1`` happens in the locked critical section
        get-or-lazy-start, BEFORE the generator yields its first frame; the
        ``finally`` drops the refcount + refreshes ``last_used`` on EVERY stream
        termination — normal completion, hang, AND consumer-abandon. Because this
        is an async generator, the ``finally`` runs when the consumer ``aclose()``s
        it (task cancellation / SSE socket teardown), so a disconnected client can
        NOT leak ``inflight`` and PERMANENTLY block reap/evict on this pool.

        The lazy-start guard (``classify_workflow``) lives inside
        ``pool.submit_stream`` and raises on the first ``__anext__`` — that raise
        propagates here AFTER the lock-release but BEFORE any frame, and the
        ``finally`` still decrements (so a guard-rejected stream doesn't leak
        inflight either). A pre-yield guard raise is the caller's signal to fall
        back in-process (PRE-YIELD-ONLY fallback)."""
        with self._lock:
            entry = self._pools.get(tenant)
            if entry is None:
                if len(self._pools) >= self.max_workers:
                    self._evict_lru_idle_locked()  # may raise PoolCapacityExceeded
                pool = self._pool_factory(
                    provider=self.provider,
                    store_root=self.store_root,
                    work_root=f"{self.work_root_base}/{tenant}",
                    tenant=tenant,
                )
                pool.start()
                entry = _Entry(pool=pool, last_used=self._clock())
                self._pools[tenant] = entry
            # inflight++ INSIDE the lock (B2): un-evictable for the stream's life.
            entry.inflight += 1
            entry.last_used = self._clock()

        lease_id = entry.pool.acquire_egress_hosts(allow_hosts)
        try:
            async for item in entry.pool.submit_stream(
                workflow=workflow,
                inputs=inputs,
                run_id=run_id,
                tenant=tenant,
                timeout=timeout,
            ):
                yield item
        finally:
            entry.pool.release_egress_hosts(lease_id)
            # Runs on normal completion, hang, AND consumer-abandon (aclose →
            # GeneratorExit) — the heart of B2 (no inflight leak on disconnect).
            with self._lock:
                entry.inflight -= 1
                entry.last_used = self._clock()  # refresh on COMPLETION (B1)

    def cancel(self, *, tenant, run_id) -> None:
        """HARD cancel ``tenant``'s run (task #483 part 1): route to
        ``pool.cancel``, which writes the ``__exec__/cancel`` marker AND
        kill+restarts the per-tenant warm worker so a MID-NODE runaway (a CodeNode
        in a busy/infinite loop, un-interruptible by the node-boundary marker
        alone) is stopped PROMPTLY + the run_dir is retained for inspection.
        Best-effort + idempotent: if the tenant has no live pool (already reaped /
        never started) it is a no-op.

        Does NOT touch ``inflight`` — that is owned exclusively by the streaming
        generator's ``finally`` in :meth:`submit_stream`. The kill makes
        ``pool.submit_stream`` observe a dead worker (or, via the route's
        ``aclose()`` cascade, receive a ``GeneratorExit``) and terminate, at which
        point the manager generator's ``finally`` decrements inflight exactly ONCE
        — no leak (the stream always ends after a kill) and no double-decrement
        (cancel never decrements). So reap/evict are not permanently blocked on a
        cancelled run.

        COLLATERAL/COLD-BOOT: see ``WarmGvisorPool.cancel`` — the shared per-tenant
        worker is killed (any other job on it dies too) and the next run on this
        tenant re-pays the cold boot once. Acceptable for the size=1 dev default +
        rare explicit cancels."""
        with self._lock:
            entry = self._pools.get(tenant)
            pool = entry.pool if entry is not None else None
        if pool is not None:
            try:
                pool.cancel(run_id=run_id, tenant=tenant)
            except Exception:
                pass

    def _evict_lru_idle_locked(self) -> None:
        """Evict the least-recently-used IDLE (``inflight == 0``) pool to free a
        worker slot. Raises ``PoolCapacityExceeded`` if every pool is in-flight
        (B1/B2 — never evict a running job). Caller holds ``self._lock``."""
        candidates = [
            (t, e) for t, e in self._pools.items() if e.inflight == 0
        ]
        if not candidates:
            raise PoolCapacityExceeded(
                f"all {len(self._pools)} warm pools are in-flight; "
                "cannot start a new tenant pool without exceeding "
                f"max_workers={self.max_workers}"
            )
        victim_tenant, victim = min(candidates, key=lambda kv: kv[1].last_used)
        victim.pool.stop()
        del self._pools[victim_tenant]

    # -- reaping -----------------------------------------------------------
    def reap(self, now=None) -> "list[str]":
        """Stop + drop every IDLE pool unused past ``idle_timeout``. NEVER reaps
        an in-flight pool (B1). Returns the list of reaped tenants. ``now`` is
        accepted for deterministic testing (defaults to the manager's clock)."""
        if now is None:
            now = self._clock()
        reaped: list[str] = []
        with self._lock:
            for tenant, entry in list(self._pools.items()):
                if entry.inflight == 0 and (now - entry.last_used) > self.idle_timeout:
                    entry.pool.stop()
                    del self._pools[tenant]
                    reaped.append(tenant)
        return reaped

    def start_reaper(self) -> None:
        """Spawn a background daemon that calls ``reap()`` every
        ``idle_timeout / 2`` seconds. Optional convenience — the deterministic
        ``reap(now)`` is the tested path."""
        if self._reaper is not None:
            return
        self._reaper_stop.clear()
        interval = max(self.idle_timeout / 2.0, 1.0)

        def _loop() -> None:
            while not self._reaper_stop.wait(interval):
                try:
                    self.reap()
                except Exception:
                    pass

        self._reaper = threading.Thread(target=_loop, daemon=True)
        self._reaper.start()

    def stop_reaper(self) -> None:
        self._reaper_stop.set()
        reaper = self._reaper
        if reaper is not None:
            reaper.join(timeout=5.0)
            self._reaper = None

    # -- shutdown ----------------------------------------------------------
    def shutdown(self) -> None:
        """Stop the reaper + every pool, and clear the fleet."""
        self.stop_reaper()
        with self._lock:
            for entry in self._pools.values():
                try:
                    entry.pool.stop()
                except Exception:
                    pass
            self._pools.clear()
