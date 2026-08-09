"""Turn-local routing for host decisions sent back into a sandbox Runtime."""

from __future__ import annotations

import asyncio
from typing import Any


class RuntimeControlRouter:
    """Route concurrent controls by Runtime source and native request id."""

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], asyncio.Future[dict[str, Any]]] = {}
        self._backlog: dict[tuple[str, str], dict[str, Any]] = {}

    @staticmethod
    def _key(source: str, request_id: str | int) -> tuple[str, str]:
        return source, str(request_id)

    def deliver(self, response: dict[str, Any]) -> None:
        correlation = response.get("correlation")
        correlation = correlation if isinstance(correlation, dict) else {}
        source = str(correlation.get("source") or "")
        request_id = correlation.get("runtime_request_id")
        if not source or request_id is None:
            return
        key = self._key(source, request_id)
        future = self._pending.pop(key, None)
        if future is not None and not future.done():
            future.set_result(response)
        else:
            self._backlog[key] = response

    async def wait(self, source: str, request_id: str | int) -> dict[str, Any]:
        key = self._key(source, request_id)
        buffered = self._backlog.pop(key, None)
        if buffered is not None:
            return buffered
        if key in self._pending:
            raise RuntimeError(f"duplicate Runtime control wait: {source}:{request_id}")
        future = asyncio.get_running_loop().create_future()
        self._pending[key] = future
        try:
            return await future
        finally:
            if self._pending.get(key) is future:
                self._pending.pop(key, None)

    def cancel(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_result({"action": "cancel", "persisted": False})
        self._pending.clear()
        self._backlog.clear()
