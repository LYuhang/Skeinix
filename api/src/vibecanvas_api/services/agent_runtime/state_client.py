"""Sandbox-side LangGraph checkpointer backed by the host Runtime State Broker.

The checkpointer intentionally owns serialization on the sandbox side.  The
host receives opaque, typed byte strings and never imports or constructs a
class named by sandbox-controlled data.  This avoids turning checkpoint RPC
into a host-side deserialization/code-execution boundary.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator, Sequence
from typing import Any
import uuid

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    CheckpointTuple,
)

from vibecanvas_engine.sandbox_bus import MSG_RUNTIME_STATE_REQUEST


_ALLOWED_SERIALIZATION_TYPES = frozenset(
    {"null", "bytes", "bytearray", "json", "msgpack"}
)
_MAX_OPAQUE_VALUE_BYTES = 32 * 1024 * 1024


def _encode_typed(serializer: Any, value: Any) -> dict[str, str]:
    value_type, data = serializer.dumps_typed(value)
    if value_type not in _ALLOWED_SERIALIZATION_TYPES:
        raise ValueError("checkpoint serializer produced an unsupported type")
    raw = bytes(data)
    if len(raw) > _MAX_OPAQUE_VALUE_BYTES:
        raise ValueError("checkpoint value exceeds the runtime state limit")
    return {
        "serialization": value_type,
        "data": base64.b64encode(raw).decode("ascii"),
    }


def _decode_typed(serializer: Any, value: dict[str, Any]) -> Any:
    value_type = str(value.get("serialization") or "")
    if value_type not in _ALLOWED_SERIALIZATION_TYPES:
        raise ValueError("runtime state contains an unsupported serialization type")
    encoded = value.get("data")
    if not isinstance(encoded, str):
        raise ValueError("runtime state value is missing its payload")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError("runtime state payload is not valid base64") from exc
    if len(raw) > _MAX_OPAQUE_VALUE_BYTES:
        raise ValueError("runtime state value exceeds the runtime state limit")
    return serializer.loads_typed((value_type, raw))


def _json_index(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-only metadata projection for host-side filtering."""
    if depth > 8:
        return None
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str):
            return value[:4096]
        return value
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:128]:
            if not isinstance(key, str):
                continue
            result[key[:256]] = _json_index(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_json_index(item, depth=depth + 1) for item in value[:128]]
    return None


class RuntimeStateRpcClient:
    """Correlated RPC client; the Runtime control loop owns all bus reads."""

    def __init__(self, channel: Any, *, timeout_s: float = 60.0) -> None:
        self._channel = channel
        self._timeout_s = timeout_s
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._send_lock = asyncio.Lock()

    async def request(self, operation: str, payload: dict[str, Any]) -> Any:
        request_id = f"state_{uuid.uuid4().hex}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            async with self._send_lock:
                await self._channel.send(
                    {
                        "type": MSG_RUNTIME_STATE_REQUEST,
                        "request": {
                            "request_id": request_id,
                            "operation": operation,
                            "payload": payload,
                        },
                    }
                )
            return await asyncio.wait_for(future, timeout=self._timeout_s)
        finally:
            self._pending.pop(request_id, None)

    def deliver(self, response: dict[str, Any]) -> None:
        request_id = str(response.get("request_id") or "")
        future = self._pending.get(request_id)
        if future is None or future.done():
            return
        if bool(response.get("ok")):
            future.set_result(response.get("result"))
            return
        error = response.get("error")
        message = (
            str(error.get("message") or "runtime state operation failed")
            if isinstance(error, dict)
            else "runtime state operation failed"
        )
        future.set_exception(RuntimeError(message))

    def fail_all(self, message: str = "runtime state broker disconnected") -> None:
        for future in tuple(self._pending.values()):
            if not future.done():
                future.set_exception(ConnectionError(message))


class BrokerCheckpointSaver(BaseCheckpointSaver):
    """Async LangGraph saver whose durable operations run on the API host."""

    def __init__(self, rpc: RuntimeStateRpcClient) -> None:
        super().__init__()
        self._rpc = rpc

    @staticmethod
    def _config(config: dict[str, Any] | None) -> dict[str, str]:
        configurable = (config or {}).get("configurable") or {}
        if not isinstance(configurable, dict):
            raise ValueError("checkpoint configurable must be an object")
        return {
            "checkpoint_ns": str(configurable.get("checkpoint_ns") or ""),
            "checkpoint_id": str(configurable.get("checkpoint_id") or ""),
        }

    def _tuple(self, value: dict[str, Any] | None) -> CheckpointTuple | None:
        if value is None:
            return None
        config = value.get("config")
        if not isinstance(config, dict):
            raise ValueError("runtime state tuple is missing config")
        checkpoint = _decode_typed(self.serde, value.get("checkpoint") or {})
        metadata = _decode_typed(self.serde, value.get("metadata") or {})
        pending_writes = []
        for item in value.get("pending_writes") or []:
            if not isinstance(item, dict):
                raise ValueError("runtime state pending write is invalid")
            pending_writes.append(
                (
                    str(item.get("task_id") or ""),
                    str(item.get("channel") or ""),
                    _decode_typed(self.serde, item.get("value") or {}),
                )
            )
        parent_config = value.get("parent_config")
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config if isinstance(parent_config, dict) else None,
            pending_writes=pending_writes,
        )

    async def aget_tuple(self, config: dict[str, Any]) -> CheckpointTuple | None:
        result = await self._rpc.request("get", self._config(config))
        if result is not None and not isinstance(result, dict):
            raise ValueError("runtime state get response is invalid")
        return self._tuple(result)

    async def alist(
        self,
        config: dict[str, Any] | None,
        *,
        filter: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        payload = self._config(config)
        payload["before_checkpoint_id"] = self._config(before).get(
            "checkpoint_id", ""
        )
        payload["limit"] = min(max(int(limit or 100), 1), 1000)
        payload["filter"] = _json_index(filter or {})
        result = await self._rpc.request("list", payload)
        if not isinstance(result, list):
            raise ValueError("runtime state list response is invalid")
        for item in result:
            if not isinstance(item, dict):
                raise ValueError("runtime state list item is invalid")
            value = self._tuple(item)
            if value is not None:
                yield value

    async def aput(
        self,
        config: dict[str, Any],
        checkpoint: dict[str, Any],
        metadata: dict[str, Any],
        new_versions: dict[str, Any],
    ) -> dict[str, Any]:
        del new_versions  # Included in the opaque checkpoint itself.
        checkpoint_id = str(checkpoint.get("id") or "")
        if not checkpoint_id:
            raise ValueError("checkpoint id is required")
        current = self._config(config)
        result = await self._rpc.request(
            "put",
            {
                "checkpoint_ns": current["checkpoint_ns"],
                "checkpoint_id": checkpoint_id,
                "parent_checkpoint_id": current["checkpoint_id"],
                "checkpoint": _encode_typed(self.serde, checkpoint),
                "metadata": _encode_typed(self.serde, metadata),
                "metadata_index": _json_index(metadata),
            },
        )
        if not isinstance(result, dict):
            raise ValueError("runtime state put response is invalid")
        return result

    async def aput_writes(
        self,
        config: dict[str, Any],
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        current = self._config(config)
        checkpoint_id = current["checkpoint_id"]
        if not checkpoint_id:
            raise ValueError("checkpoint id is required for pending writes")
        await self._rpc.request(
            "put_writes",
            {
                "checkpoint_ns": current["checkpoint_ns"],
                "checkpoint_id": checkpoint_id,
                "task_id": str(task_id),
                "task_path": str(task_path),
                "writes": [
                    {
                        "index": WRITES_IDX_MAP.get(str(channel), index),
                        "channel": str(channel),
                        "value": _encode_typed(self.serde, value),
                    }
                    for index, (channel, value) in enumerate(writes)
                ],
            },
        )

    async def adelete_thread(self, thread_id: str) -> None:
        # The host ignores this argument and deletes only the thread bound to the
        # current Runtime capability.  Sending it is useful for mismatch tests.
        await self._rpc.request("delete_thread", {"thread_id": str(thread_id)})

    # A graph using this saver must use its async execution APIs.  Failing
    # explicitly is safer than attempting a nested event loop in the sandbox.
    def get_tuple(self, config: dict[str, Any]):  # pragma: no cover - guardrail
        raise RuntimeError("Runtime State Broker is async-only")

    def list(self, *args: Any, **kwargs: Any):  # pragma: no cover - guardrail
        raise RuntimeError("Runtime State Broker is async-only")

    def put(self, *args: Any, **kwargs: Any):  # pragma: no cover - guardrail
        raise RuntimeError("Runtime State Broker is async-only")

    def put_writes(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise RuntimeError("Runtime State Broker is async-only")

    def delete_thread(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise RuntimeError("Runtime State Broker is async-only")


__all__ = [
    "BrokerCheckpointSaver",
    "RuntimeStateRpcClient",
]
