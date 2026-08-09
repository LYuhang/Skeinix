from __future__ import annotations

from types import SimpleNamespace

import pytest

from vibecanvas_api.agents.middleware.runtime_resilience import (
    RuntimeResilienceMiddleware,
)


@pytest.mark.asyncio
async def test_retries_only_known_read_tool() -> None:
    attempts = 0

    async def flaky(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("reset")
        return "ok"

    holder = {}
    middleware = RuntimeResilienceMiddleware(trace_holder=holder)
    request = SimpleNamespace(tool_call={"name": "read_file"})
    assert await middleware.awrap_tool_call(request, flaky) == "ok"
    assert attempts == 2
    assert holder["runtime_limits"]["retry_count"] == 1


@pytest.mark.asyncio
async def test_never_retries_side_effecting_tool() -> None:
    attempts = 0

    async def broken(_request):
        nonlocal attempts
        attempts += 1
        raise OSError("unknown commit outcome")

    middleware = RuntimeResilienceMiddleware(read_tool_retries=1)
    request = SimpleNamespace(tool_call={"name": "deployment_create"})
    with pytest.raises(OSError, match="unknown commit outcome"):
        await middleware.awrap_tool_call(request, broken)
    assert attempts == 1


@pytest.mark.asyncio
async def test_call_budget_fails_before_extra_execution() -> None:
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return "ok"

    middleware = RuntimeResilienceMiddleware(max_tool_calls=1)
    request = SimpleNamespace(tool_call={"name": "read_file"})
    assert await middleware.awrap_tool_call(request, handler) == "ok"
    with pytest.raises(RuntimeError, match="tool_call_limit"):
        await middleware.awrap_tool_call(request, handler)
    assert calls == 1


@pytest.mark.asyncio
async def test_retries_openai_compatible_error_envelope(monkeypatch) -> None:
    attempts = 0

    async def flaky(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ValueError({
                "message": "upstream worker saturated",
                "code": 502,
            })
        return "ok"

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "vibecanvas_api.agents.middleware.runtime_resilience.asyncio.sleep",
        no_wait,
    )
    middleware = RuntimeResilienceMiddleware(model_retries=1)
    assert await middleware.awrap_model_call(object(), flaky) == "ok"
    assert attempts == 2


@pytest.mark.asyncio
async def test_does_not_retry_non_transient_provider_error() -> None:
    attempts = 0

    async def broken(_request):
        nonlocal attempts
        attempts += 1
        raise ValueError({"message": "invalid request", "code": 400})

    middleware = RuntimeResilienceMiddleware(model_retries=2)
    with pytest.raises(ValueError, match="invalid request"):
        await middleware.awrap_model_call(object(), broken)
    assert attempts == 1
