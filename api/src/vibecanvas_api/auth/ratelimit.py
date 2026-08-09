"""Authentication rate limiting for development and multi-replica deploys.

Production uses a Redis-backed atomic sliding window.  Each request first adds
a provisional member; successful authentication removes only its own member,
while failures remain until the window expires.  This avoids both concurrent
check/record races and one successful request erasing another request's failure.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import secrets
import time
from collections import defaultdict
from typing import Any

from vibecanvas_api.config import config


class LoginRateLimitExceeded(Exception):
    """The principal/network failure window has reached its cap."""


class LoginRateLimitUnavailable(Exception):
    """The configured distributed security boundary is unavailable."""


class LoginRateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self._max = max_attempts
        self._window = window_seconds
        self._failures: dict[str, list[float]] = defaultdict(list)

    def _prune(self, key: str) -> None:
        cutoff = time.monotonic() - self._window
        kept = [t for t in self._failures[key] if t > cutoff]
        if kept:
            self._failures[key] = kept
        else:
            self._failures.pop(key, None)

    def check(self, key: str) -> bool:
        """True = allowed, False = blocked."""
        self._prune(key)
        return len(self._failures[key]) < self._max

    def record_failure(self, key: str) -> None:
        self._failures[key].append(time.monotonic())

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)


_BEGIN_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local cutoff_ms = tonumber(ARGV[2])
local member = ARGV[3]
local max_attempts = tonumber(ARGV[4])
local window_ms = tonumber(ARGV[5])
redis.call('ZREMRANGEBYSCORE', key, '-inf', cutoff_ms)
local count = redis.call('ZCARD', key)
if count >= max_attempts then
  return 0
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return 1
"""

_SUCCESS_LUA = """
local removed = redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('ZCARD', KEYS[1]) == 0 then
  redis.call('DEL', KEYS[1])
end
return removed
"""

_redis_client: Any = None
_local_limiter = LoginRateLimiter(max_attempts=5, window_seconds=300)
_local_action_limiters: dict[tuple[int, int], LoginRateLimiter] = {}


def _get_redis() -> Any:
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(
            config.redis.url,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    except Exception as exc:  # import/config failure is fail-closed in prod
        raise LoginRateLimitUnavailable from exc
    return _redis_client


@dataclass(frozen=True, slots=True)
class LoginAttempt:
    """One admitted authentication attempt with isolated success cleanup."""

    key: str
    member: str
    distributed: bool

    async def failure(self) -> None:
        if not self.distributed:
            _local_limiter.record_failure(self.key)
        # Redis attempts are provisional: leaving the member is the failure.

    async def success(self) -> None:
        if not self.distributed:
            _local_limiter.record_success(self.key)
            return
        try:
            await _get_redis().eval(_SUCCESS_LUA, 1, self.key, self.member)
        except Exception as exc:
            raise LoginRateLimitUnavailable from exc


async def begin_login_attempt(raw_key: str) -> LoginAttempt:
    """Admit one attempt or raise a typed fail-closed rate-limit error."""
    if not config.distributed_auth_rate_limit_enabled:
        if not _local_limiter.check(raw_key):
            raise LoginRateLimitExceeded
        return LoginAttempt(key=raw_key, member="", distributed=False)

    # Redis keys are operational metadata and must not reveal email/IP values.
    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    key = f"auth:login:failures:{digest}"
    member = secrets.token_urlsafe(18)
    now_ms = int(time.time() * 1000)
    window_ms = 300_000
    try:
        admitted = await _get_redis().eval(
            _BEGIN_LUA,
            1,
            key,
            now_ms,
            now_ms - window_ms,
            member,
            5,
            window_ms,
        )
    except LoginRateLimitUnavailable:
        raise
    except Exception as exc:
        raise LoginRateLimitUnavailable from exc
    if int(admitted) != 1:
        raise LoginRateLimitExceeded
    return LoginAttempt(key=key, member=member, distributed=True)


async def consume_rate_limited_action(
    raw_key: str,
    *,
    max_attempts: int,
    window_seconds: int,
) -> None:
    """Consume a bounded unauthenticated action slot across all replicas.

    Unlike password login, successful requests remain in this window. This is
    intended for resource-consuming pre-authentication steps such as OIDC
    discovery/start/callback, not ordinary authenticated business traffic.
    """
    if max_attempts < 1 or window_seconds < 1:
        raise ValueError("rate limit bounds must be positive")
    if not config.distributed_auth_rate_limit_enabled:
        limiter = _local_action_limiters.setdefault(
            (max_attempts, window_seconds),
            LoginRateLimiter(
                max_attempts=max_attempts,
                window_seconds=window_seconds,
            ),
        )
        if not limiter.check(raw_key):
            raise LoginRateLimitExceeded
        limiter.record_failure(raw_key)
        return

    digest = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    key = f"auth:action:attempts:{digest}"
    member = secrets.token_urlsafe(18)
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    try:
        admitted = await _get_redis().eval(
            _BEGIN_LUA,
            1,
            key,
            now_ms,
            now_ms - window_ms,
            member,
            max_attempts,
            window_ms,
        )
    except LoginRateLimitUnavailable:
        raise
    except Exception as exc:
        raise LoginRateLimitUnavailable from exc
    if int(admitted) != 1:
        raise LoginRateLimitExceeded
