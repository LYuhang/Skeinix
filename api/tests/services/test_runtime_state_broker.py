from __future__ import annotations

import asyncio
import base64
import uuid

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph
import pytest
from sqlalchemy import text

from vibecanvas_api.config import config
from vibecanvas_api.services.agent_runtime.checkpoint_store import (
    LangChainCheckpointStore,
    RuntimeStateScope,
    runtime_state_response,
)
from vibecanvas_api.services.agent_runtime.state_client import (
    BrokerCheckpointSaver,
    RuntimeStateRpcClient,
)


def _opaque(data: bytes, serialization: str = "bytes") -> dict[str, str]:
    return {
        "serialization": serialization,
        "data": base64.b64encode(data).decode("ascii"),
    }


def _request(operation: str, payload: dict) -> dict:
    return {
        "request_id": f"state_{uuid.uuid4().hex}",
        "operation": operation,
        "payload": payload,
    }


async def _seed_tenant(pg_engine) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    async with pg_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO tenants(tenant_id, name) "
                "VALUES (:tenant_id, 'runtime-state-test')"
            ),
            {"tenant_id": tenant_id},
        )
    return tenant_id


@pytest.mark.asyncio
async def test_empty_migration_backfill_is_valid_psycopg_sql():
    """Parameterized LIKE patterns must escape psycopg's percent marker."""
    store = LangChainCheckpointStore(config.database.url)
    try:
        assert await store.backfill_encryption() == 0
        assert await store.plaintext_row_count() == 0
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_host_store_is_scope_bound_and_persists_opaque_values(pg_engine):
    store = LangChainCheckpointStore(config.database.url)
    suffix = uuid.uuid4().hex
    tenant_id = await _seed_tenant(pg_engine)
    scope = RuntimeStateScope(
        organization_id=str(tenant_id),
        chat_id=f"chat-{suffix}",
        runtime_session_id=f"session-{suffix}",
        thread_id=f"thread-{suffix}",
    )
    checkpoint = b"opaque-checkpoint-" + suffix.encode()
    metadata = b"opaque-metadata-" + suffix.encode()
    try:
        put = await runtime_state_response(
            store,
            scope,
            _request(
                "put",
                {
                    "checkpoint_ns": "",
                    "checkpoint_id": "0002",
                    "parent_checkpoint_id": "0001",
                    "checkpoint": _opaque(checkpoint),
                    "metadata": _opaque(metadata),
                    "metadata_index": {"source": "loop", "step": 2},
                },
            ),
        )
        assert put["ok"] is True

        writes = await runtime_state_response(
            store,
            scope,
            _request(
                "put_writes",
                {
                    "checkpoint_ns": "",
                    "checkpoint_id": "0002",
                    "task_id": "task-1",
                    "task_path": "pull",
                    "writes": [
                        {
                            "index": 0,
                            "channel": "messages",
                            "value": _opaque(b"pending"),
                        }
                    ],
                },
            ),
        )
        assert writes["ok"] is True

        loaded = await runtime_state_response(
            store,
            scope,
            _request("get", {"checkpoint_ns": "", "checkpoint_id": "0002"}),
        )
        assert loaded["ok"] is True
        assert base64.b64decode(loaded["result"]["checkpoint"]["data"]) == checkpoint
        assert base64.b64decode(loaded["result"]["metadata"]["data"]) == metadata
        assert len(loaded["result"]["pending_writes"]) == 1

        other_scope = RuntimeStateScope(
            organization_id=scope.organization_id,
            chat_id=f"other-{scope.chat_id}",
            runtime_session_id=scope.runtime_session_id,
            thread_id=scope.thread_id,
        )
        isolated = await runtime_state_response(
            store,
            other_scope,
            _request("get", {"checkpoint_ns": "", "checkpoint_id": "0002"}),
        )
        assert isolated == {
            "request_id": isolated["request_id"],
            "ok": True,
            "result": None,
        }

        mismatch = await runtime_state_response(
            store,
            scope,
            _request("delete_thread", {"thread_id": "another-thread"}),
        )
        assert mismatch["ok"] is False
        assert mismatch["error"]["code"] == "invalid_state_request"

        async with pg_engine.connect() as connection:
            raw = (
                await connection.execute(
                    text(
                        "SELECT checkpoint_payload FROM vc_runtime_checkpoints "
                        "WHERE organization_id=:organization_id AND chat_id=:chat_id"
                    ),
                    {
                        "organization_id": scope.organization_id,
                        "chat_id": scope.chat_id,
                    },
                )
            ).scalar_one()
            assert checkpoint not in bytes(raw)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_host_store_encrypts_checkpoint_payloads_without_changing_rpc_shape(
    pg_engine,
):
    tenant_id = await _seed_tenant(pg_engine)
    suffix = uuid.uuid4().hex
    store = LangChainCheckpointStore(config.database.url)
    scope = RuntimeStateScope(
        organization_id=str(tenant_id),
        chat_id=f"chat-{suffix}",
        runtime_session_id=f"session-{suffix}",
        thread_id=f"thread-{suffix}",
    )
    checkpoint = b"private-checkpoint-" + suffix.encode()
    metadata = b"private-metadata-" + suffix.encode()
    try:
        put = await runtime_state_response(
            store,
            scope,
            _request(
                "put",
                {
                    "checkpoint_ns": "",
                    "checkpoint_id": "0001",
                    "parent_checkpoint_id": "",
                    "checkpoint": _opaque(checkpoint),
                    "metadata": _opaque(metadata),
                    "metadata_index": {"source": "loop", "step": 1},
                },
            ),
        )
        assert put["ok"] is True
        writes = await runtime_state_response(
            store,
            scope,
            _request(
                "put_writes",
                {
                    "checkpoint_ns": "",
                    "checkpoint_id": "0001",
                    "task_id": "task-1",
                    "task_path": "pull",
                    "writes": [
                        {
                            "index": 0,
                            "channel": "messages",
                            "value": _opaque(b"private-pending-write"),
                        }
                    ],
                },
            ),
        )
        assert writes["ok"] is True

        async with pg_engine.connect() as connection:
            row = (
                await connection.execute(
                    text(
                        "SELECT checkpoint_serialization, checkpoint_payload, "
                        "metadata_payload FROM vc_runtime_checkpoints "
                        "WHERE organization_id=:organization_id AND chat_id=:chat_id"
                    ),
                    {
                        "organization_id": scope.organization_id,
                        "chat_id": scope.chat_id,
                    },
                )
            ).mappings().one()
            raw_write = (
                await connection.execute(
                    text(
                        "SELECT value_payload FROM vc_runtime_checkpoint_writes "
                        "WHERE organization_id=:organization_id AND chat_id=:chat_id"
                    ),
                    {
                        "organization_id": scope.organization_id,
                        "chat_id": scope.chat_id,
                    },
                )
            ).scalar_one()
        assert row["checkpoint_serialization"] == "vcenc1:bytes"
        assert checkpoint not in bytes(row["checkpoint_payload"])
        assert metadata not in bytes(row["metadata_payload"])
        assert b"private-pending-write" not in bytes(raw_write)

        loaded = await runtime_state_response(
            store,
            scope,
            _request("get", {"checkpoint_ns": "", "checkpoint_id": "0001"}),
        )
        assert loaded["ok"] is True
        assert base64.b64decode(loaded["result"]["checkpoint"]["data"]) == checkpoint
        assert base64.b64decode(loaded["result"]["metadata"]["data"]) == metadata
        assert base64.b64decode(
            loaded["result"]["pending_writes"][0]["value"]["data"]
        ) == b"private-pending-write"
    finally:
        await store.close()


class _CaptureChannel:
    def __init__(self) -> None:
        self.messages: asyncio.Queue[dict] = asyncio.Queue()

    async def send(self, message: dict) -> None:
        await self.messages.put(message)


@pytest.mark.asyncio
async def test_checkpoint_values_are_decoded_only_by_sandbox_client():
    channel = _CaptureChannel()
    rpc = RuntimeStateRpcClient(channel, timeout_s=2)
    saver = BrokerCheckpointSaver(rpc)
    checkpoint = {
        "v": 4,
        "id": "checkpoint-1",
        "ts": "2026-07-31T00:00:00Z",
        "channel_values": {"messages": [HumanMessage(content="private text")]},
        "channel_versions": {},
        "versions_seen": {},
        "updated_channels": None,
    }
    config_in = {
        "configurable": {
            "thread_id": "sandbox-thread",
            "checkpoint_ns": "",
        }
    }
    put_task = asyncio.create_task(
        saver.aput(config_in, checkpoint, {"source": "input", "step": 1}, {})
    )
    envelope = await channel.messages.get()
    state_request = envelope["request"]
    rpc.deliver(
        {
            "request_id": state_request["request_id"],
            "ok": True,
            "result": {
                "configurable": {
                    "thread_id": "sandbox-thread",
                    "checkpoint_ns": "",
                    "checkpoint_id": "checkpoint-1",
                }
            },
        }
    )
    next_config = await put_task
    assert next_config["configurable"]["checkpoint_id"] == "checkpoint-1"

    get_task = asyncio.create_task(saver.aget_tuple(next_config))
    get_envelope = await channel.messages.get()
    rpc.deliver(
        {
            "request_id": get_envelope["request"]["request_id"],
            "ok": True,
            "result": {
                "config": next_config,
                "checkpoint": state_request["payload"]["checkpoint"],
                "metadata": state_request["payload"]["metadata"],
                "parent_config": None,
                "pending_writes": [],
            },
        }
    )
    loaded = await get_task
    assert loaded is not None
    message = loaded.checkpoint["channel_values"]["messages"][0]
    assert isinstance(message, HumanMessage)
    assert message.content == "private text"


@pytest.mark.asyncio
async def test_state_broker_rejects_pickle_and_oversized_identity():
    store = LangChainCheckpointStore(config.database.url)
    scope = RuntimeStateScope("org", "chat", "session", "thread")
    try:
        response = await runtime_state_response(
            store,
            scope,
            _request(
                "put",
                {
                    "checkpoint_ns": "",
                    "checkpoint_id": "cp",
                    "parent_checkpoint_id": "",
                    "checkpoint": _opaque(b"pickle", "pickle"),
                    "metadata": _opaque(b"metadata"),
                    "metadata_index": {},
                },
            ),
        )
        assert response["ok"] is False
        assert response["error"]["code"] == "invalid_state_request"
    finally:
        await store.close()


class _LoopbackStateChannel:
    def __init__(
        self,
        store: LangChainCheckpointStore,
        scope: RuntimeStateScope,
    ) -> None:
        self.store = store
        self.scope = scope
        self.rpc: RuntimeStateRpcClient | None = None

    async def send(self, envelope: dict) -> None:
        assert self.rpc is not None
        response = await runtime_state_response(
            self.store,
            self.scope,
            envelope["request"],
        )
        self.rpc.deliver(response)


@pytest.mark.asyncio
async def test_real_langgraph_round_trips_through_state_broker(pg_engine):
    suffix = uuid.uuid4().hex
    tenant_id = await _seed_tenant(pg_engine)
    store = LangChainCheckpointStore(config.database.url)
    scope = RuntimeStateScope(
        organization_id=str(tenant_id),
        chat_id=f"chat-{suffix}",
        runtime_session_id=f"session-{suffix}",
        thread_id=f"thread-{suffix}",
    )
    channel = _LoopbackStateChannel(store, scope)
    rpc = RuntimeStateRpcClient(channel, timeout_s=5)
    channel.rpc = rpc
    saver = BrokerCheckpointSaver(rpc)

    async def answer(state: MessagesState) -> dict:
        return {"messages": [{"role": "assistant", "content": "ack"}]}

    builder = StateGraph(MessagesState)
    builder.add_node("answer", answer)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", END)
    graph = builder.compile(checkpointer=saver)
    invocation = {
        "configurable": {
            "thread_id": scope.thread_id,
            "checkpoint_ns": "",
        }
    }
    try:
        first = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "first"}]},
            invocation,
        )
        second = await graph.ainvoke(
            {"messages": [{"role": "user", "content": "second"}]},
            invocation,
        )
        assert [message.content for message in first["messages"]] == ["first", "ack"]
        assert [message.content for message in second["messages"]] == [
            "first",
            "ack",
            "second",
            "ack",
        ]
    finally:
        await store.close()
