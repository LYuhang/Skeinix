from vibecanvas_api.config import AppConfig


def test_sandbox_max_concurrent_default_is_8(monkeypatch):
    monkeypatch.delenv("SANDBOX_MAX_CONCURRENT", raising=False)
    cfg = AppConfig({})
    assert cfg.sandbox_max_concurrent == 8


def test_sandbox_max_concurrent_env_override(monkeypatch):
    monkeypatch.setenv("SANDBOX_MAX_CONCURRENT", "3")
    cfg = AppConfig({})
    assert cfg.sandbox_max_concurrent == 3


import pytest
from vibecanvas_api.services.sandbox.admission import (
    SandboxCapacityExceeded, configure_admission, sandbox_admission, _inflight,
)


@pytest.mark.asyncio
async def test_admission_allows_up_to_cap_then_rejects_nonblocking():
    configure_admission(2)
    async with sandbox_admission():
        async with sandbox_admission():
            assert _inflight() == 2
            with pytest.raises(SandboxCapacityExceeded):
                async with sandbox_admission(block=False):
                    pass
    assert _inflight() == 0


@pytest.mark.asyncio
async def test_admission_releases_on_exception():
    configure_admission(1)
    with pytest.raises(ValueError):
        async with sandbox_admission():
            raise ValueError("boom")
    assert _inflight() == 0
    async with sandbox_admission():
        assert _inflight() == 1


def test_redis_admission_bounds_globally():
    import fakeredis, threading, time
    from vibecanvas_api.services.sandbox.redis_admission import (
        RedisAdmission, _peak_for_test, _peak_reset_for_test)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    adm = RedisAdmission(r, cap=2, key="sbx:test")
    _peak_reset_for_test()
    def worker():
        try:
            with adm.slot(tenant_id="t1"): time.sleep(0.15)
        except Exception: pass
    ts=[threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert _peak_for_test() <= 2
    assert int(r.get("sbx:test") or 0) == 0   # all slots released


def test_redis_admission_releases_on_exception():
    import fakeredis
    from vibecanvas_api.services.sandbox.redis_admission import (
        RedisAdmission)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    adm = RedisAdmission(r, cap=1, key="sbx:exc")
    try:
        with adm.slot(tenant_id="t1"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert int(r.get("sbx:exc") or 0) == 0
    # cap of 1 frees up again
    with adm.slot(tenant_id="t1"):
        assert int(r.get("sbx:exc") or 0) == 1
    assert int(r.get("sbx:exc") or 0) == 0


def test_redis_admission_nonblocking_raises_at_cap():
    import fakeredis
    from vibecanvas_api.services.sandbox.redis_admission import (
        RedisAdmission, SandboxCapacityExceeded)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    adm = RedisAdmission(r, cap=1, key="sbx:nb")
    with adm.slot(tenant_id="t1"):
        with pytest.raises(SandboxCapacityExceeded):
            with adm.slot(tenant_id="t2", block=False):
                pass
    assert int(r.get("sbx:nb") or 0) == 0


def test_redis_admission_blocks_until_slot_frees():
    """C1: with block=True at cap, the second acquirer must WAIT for a slot to
    free (queue) and then SUCCEED — NOT raise SandboxCapacityExceeded."""
    import fakeredis, threading, time
    from vibecanvas_api.services.sandbox.redis_admission import (
        RedisAdmission, _peak_reset_for_test, _peak_for_test)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    adm = RedisAdmission(r, cap=1, key="sbx:block", poll_interval_s=0.01)
    _peak_reset_for_test()

    ran: list[str] = []
    a_holding = threading.Event()
    b_attempted = threading.Event()

    def a():
        with adm.slot(tenant_id="ta"):
            a_holding.set()
            # hold while B is provably blocked, then release
            b_attempted.wait(timeout=2.0)
            time.sleep(0.05)
            ran.append("a")

    def b():
        a_holding.wait(timeout=2.0)
        b_attempted.set()
        # B must block here until A releases, then run (no exception)
        with adm.slot(tenant_id="tb", block=True):
            ran.append("b")

    tb = threading.Thread(target=b)
    ta = threading.Thread(target=a)
    tb.start()
    ta.start()
    ta.join(timeout=5.0)
    tb.join(timeout=5.0)

    assert ran == ["a", "b"], f"B did not queue-then-run after A: {ran!r}"
    assert _peak_for_test() <= 1, "never more than cap held concurrently"
    assert int(r.get("sbx:block") or 0) == 0, "all slots released"


def test_redis_admission_block_false_raises_at_cap():
    """C1: block=False keeps the raise-immediately-at-cap behavior."""
    import fakeredis
    from vibecanvas_api.services.sandbox.redis_admission import (
        RedisAdmission, SandboxCapacityExceeded)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer())
    adm = RedisAdmission(r, cap=1, key="sbx:bf")
    with adm.slot(tenant_id="t1"):
        with pytest.raises(SandboxCapacityExceeded):
            with adm.slot(tenant_id="t2", block=False):
                pass
    assert int(r.get("sbx:bf") or 0) == 0


def test_sync_sandbox_admission_failsoft_without_redis():
    # Redis unavailable → falls back to a process semaphore, never crashes.
    from vibecanvas_api.services.sandbox import admission as adm_mod
    adm_mod.configure_admission(2)
    with adm_mod.sync_sandbox_admission(tenant_id="t1"):
        with adm_mod.sync_sandbox_admission(tenant_id="t1"):
            pass
