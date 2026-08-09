from __future__ import annotations

import hashlib

import pytest

from vibecanvas_api.auth import ratelimit


class FakeRedis:
    def __init__(self, *, admitted: int = 1, fail: bool = False):
        self.admitted = admitted
        self.fail = fail
        self.calls: list[tuple] = []

    async def eval(self, *args):
        self.calls.append(args)
        if self.fail:
            raise ConnectionError("redis unavailable")
        if args[0] == ratelimit._BEGIN_LUA:
            return self.admitted
        return 1


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ratelimit, "_redis_client", None)
    monkeypatch.setattr(
        ratelimit.config,
        "distributed_auth_rate_limit_enabled",
        True,
    )


@pytest.mark.asyncio
async def test_distributed_attempt_uses_hashed_key_and_isolated_success(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "_get_redis", lambda: fake)

    attempt = await ratelimit.begin_login_attempt("203.0.113.7:user@example.test")
    await attempt.success()

    assert len(fake.calls) == 2
    begin = fake.calls[0]
    expected = hashlib.sha256(
        b"203.0.113.7:user@example.test"
    ).hexdigest()
    assert begin[2] == f"auth:login:failures:{expected}"
    assert "user@example.test" not in begin[2]
    assert fake.calls[1][2] == begin[2]
    assert fake.calls[1][3] == begin[5]


@pytest.mark.asyncio
async def test_distributed_failure_retains_provisional_member(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "_get_redis", lambda: fake)

    attempt = await ratelimit.begin_login_attempt("key")
    await attempt.failure()

    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_distributed_limit_and_outage_fail_closed(monkeypatch):
    blocked = FakeRedis(admitted=0)
    monkeypatch.setattr(ratelimit, "_get_redis", lambda: blocked)
    with pytest.raises(ratelimit.LoginRateLimitExceeded):
        await ratelimit.begin_login_attempt("key")

    down = FakeRedis(fail=True)
    monkeypatch.setattr(ratelimit, "_get_redis", lambda: down)
    with pytest.raises(ratelimit.LoginRateLimitUnavailable):
        await ratelimit.begin_login_attempt("key")
