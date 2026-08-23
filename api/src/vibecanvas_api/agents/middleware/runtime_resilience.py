"""Per-turn call budgets and conservative transient retry policy."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware


_RETRYABLE_READ_TOOLS = {
    "read_file", "grep", "read_images", "web_search",
    "background_job_list", "list_workflows", "get_workflow",
    "knowledge_list", "knowledge_get", "knowledge_create",
    "knowledge_update", "knowledge_delete", "knowledge_search",
    "task_list", "task_get", "deployment_list", "deployment_get",
}
_TRANSIENT = (TimeoutError, OSError, ConnectionError)
_TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429})


def _provider_status_code(exc: BaseException) -> int | None:
    """Extract an HTTP-like status from provider/SDK exceptions.

    Some OpenAI-compatible gateways return an error object in a successful HTTP
    envelope.  The SDK then raises ``ValueError({"code": 502, ...})`` instead
    of an HTTP exception, so checking exception classes alone misses a genuinely
    retryable model call.
    """
    direct = getattr(exc, "status_code", None)
    if isinstance(direct, int):
        return direct
    for arg in getattr(exc, "args", ()):
        if not isinstance(arg, dict):
            continue
        code = arg.get("code") or arg.get("status_code")
        try:
            return int(code)
        except (TypeError, ValueError):
            continue
    return None


def _is_transient_model_error(exc: BaseException) -> bool:
    if isinstance(exc, _TRANSIENT):
        return True
    status = _provider_status_code(exc)
    return bool(
        status in _TRANSIENT_STATUS_CODES
        or (status is not None and 500 <= status <= 599)
    )


class RuntimeResilienceMiddleware(AgentMiddleware):
    """Bound a Turn and retry only operations without external side effects."""

    def __init__(
        self,
        *,
        max_model_calls: int = 32,
        max_tool_calls: int = 64,
        wall_clock_s: float = 900.0,
        model_retries: int = 1,
        read_tool_retries: int = 1,
        trace_holder: dict | None = None,
    ) -> None:
        super().__init__()
        self.max_model_calls = max(1, int(max_model_calls))
        self.max_tool_calls = max(1, int(max_tool_calls))
        self.wall_clock_s = max(1.0, float(wall_clock_s))
        self.model_retries = max(0, min(2, int(model_retries)))
        self.read_tool_retries = max(0, min(2, int(read_tool_retries)))
        self.trace_holder = trace_holder
        self.started = monotonic()
        self.model_calls = 0
        self.tool_calls = 0
        self.retry_count = 0
        self._publish()

    def _remaining(self) -> float:
        remaining = self.wall_clock_s - (monotonic() - self.started)
        if remaining <= 0:
            raise RuntimeError("agent_turn_wall_clock_limit_exceeded")
        return remaining

    def _publish(self) -> None:
        if self.trace_holder is not None:
            self.trace_holder["runtime_limits"] = {
                "max_model_calls": self.max_model_calls,
                "max_tool_calls": self.max_tool_calls,
                "wall_clock_s": self.wall_clock_s,
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "retry_count": self.retry_count,
                "model_retry_limit": self.model_retries,
                "read_tool_retry_limit": self.read_tool_retries,
                "fallback": "disabled_no_configured_safe_alternate",
            }

    async def _bounded(self, handler: Callable[[Any], Awaitable[Any]], request: Any) -> Any:
        return await asyncio.wait_for(handler(request), timeout=self._remaining())

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        self.model_calls += 1
        if self.model_calls > self.max_model_calls:
            self._publish()
            raise RuntimeError("agent_model_call_limit_exceeded")
        attempts = 0
        while True:
            try:
                result = await self._bounded(handler, request)
                self._publish()
                return result
            except Exception as exc:
                if not _is_transient_model_error(exc) or attempts >= self.model_retries:
                    self._publish()
                    raise
                attempts += 1
                self.retry_count += 1
                self._publish()
                # Capacity/rate-limit responses need a small pause; an immediate
                # retry commonly lands on the same saturated upstream worker.
                await asyncio.sleep(float(2 ** (attempts - 1)))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        self.tool_calls += 1
        if self.tool_calls > self.max_tool_calls:
            self._publish()
            raise RuntimeError("agent_tool_call_limit_exceeded")
        tool_call = getattr(request, "tool_call", None) or {}
        name = str(tool_call.get("name") or "")
        retry_limit = self.read_tool_retries if name in _RETRYABLE_READ_TOOLS else 0
        attempts = 0
        while True:
            try:
                result = await self._bounded(handler, request)
                self._publish()
                return result
            except _TRANSIENT:
                if attempts >= retry_limit:
                    self._publish()
                    raise
                attempts += 1
                self.retry_count += 1
                self._publish()
