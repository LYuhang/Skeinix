from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from vibecanvas_api.auth.ratelimit import LoginRateLimitExceeded
from vibecanvas_api.routes.auth import (
    LoginIn,
    RegisterIn,
    ResetConfirmIn,
    _consume_unauthenticated_rate_limit,
)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: RegisterIn(
            email="user@example.com",
            username="user",
            password="x" * 1025,
        ),
        lambda: LoginIn(email="user@example.com", password="x" * 1025),
        lambda: ResetConfirmIn(
            reset_token="t" * 32,
            new_password="x" * 1025,
        ),
    ],
)
def test_auth_models_reject_oversized_passwords(factory):
    with pytest.raises(ValidationError):
        factory()


def test_resource_consuming_auth_limit_keeps_successful_slot():
    attempt = AsyncMock()

    async def run():
        with patch(
            "vibecanvas_api.routes.auth.begin_login_attempt",
            new=AsyncMock(return_value=attempt),
        ):
            await _consume_unauthenticated_rate_limit("register:203.0.113.4")

    asyncio.run(run())
    attempt.failure.assert_awaited_once_with()
    attempt.success.assert_not_awaited()


def test_resource_consuming_auth_limit_maps_exhaustion_to_429():
    async def run():
        with patch(
            "vibecanvas_api.routes.auth.begin_login_attempt",
            new=AsyncMock(side_effect=LoginRateLimitExceeded),
        ):
            await _consume_unauthenticated_rate_limit("register:203.0.113.4")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 429
