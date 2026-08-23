from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.services.agent_runtime.orchestrator import (
    AgentRuntimeOrchestrator,
    _product_events,
    private_runtime_root,
)
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeEvent,
    RuntimeOpenRequest,
    RuntimeTurnRequest,
    RuntimeType,
)
from vibecanvas_api.storage.agent_runs_repo import AgentRunsRepo
from vibecanvas_api.storage.agent_runtime_repo import AgentRuntimeRepo
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.hitl_repo import HitlRepo


class _Sandbox:
    def __init__(self):
        self.controls = []

    async def run_agent_runtime_stream(self, request):
        common = {
            "chat_id": request["chat_id"],
            "turn_id": request["turn_id"],
            "runtime_type": "langchain",
            "runtime_session_id": request["runtime_session_id"],
        }
        yield {**common, "event_id": "start", "seq": 1, "type": "runtime.started"}
        yield {
            **common,
            "event_id": "projection",
            "seq": 2,
            "type": "projection",
            "payload": {
                "event_type": "CHAT_EVENT",
                "payload": {"type": "message_replace", "content": "hello"},
            },
        }
        yield {**common, "event_id": "done", "seq": 3, "type": "runtime.completed"}

    async def cancel_agent_runtime(self, _turn_id):
        return True

    async def send_agent_runtime_control(self, turn_id, response):
        self.controls.append((turn_id, response))


class _Manager:
    def __init__(self):
        self.session = _Sandbox()
        self.calls = []

    async def get_session(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.session

    async def get_loaded_session(self, *args):
        self.calls.append((args, {"loaded_only": True}))
        return self.session


class _RuntimeStateStore:
    def __init__(self):
        self.deleted = []

    async def delete(self, state_ref):
        self.deleted.append(state_ref)
        return True


def _runtime_event(
    event_type: str,
    *,
    seq: int,
    payload: dict | None = None,
) -> RuntimeEvent:
    return RuntimeEvent(
        event_id=f"event-{seq}",
        seq=seq,
        chat_id="chat",
        turn_id="turn",
        runtime_type="langchain",
        runtime_session_id="runtime",
        type=event_type,
        payload=payload or {},
    )


def test_interaction_required_projects_shared_waiting_state_and_form() -> None:
    projection = {
        "type": "tool_update",
        "tool_call_id": "input-1",
        "status": "running",
        "artifact": {"payload": {"kind": "interactive_artifact"}},
    }
    event = _runtime_event(
        "interaction.required",
        seq=1,
        payload={
            "hitl_request_id": "hitl-input-1",
            "resume_mode": "same_turn",
            "projection_event": projection,
        },
    )

    assert _product_events(event) == [
        (
            "INTERACTION_REQUIRED",
            {
                "hitl_request_id": "hitl-input-1",
                "resume_mode": "same_turn",
                "projection_event": projection,
            },
        ),
        ("CHAT_EVENT", projection),
    ]


@pytest.mark.asyncio
async def test_orchestrator_projects_sdk_neutral_events_and_uses_resident_chat_session():
    manager = _Manager()
    orchestrator = AgentRuntimeOrchestrator(manager)
    root = private_runtime_root(RuntimeType.LANGCHAIN, "chat/unsafe")
    open_request = RuntimeOpenRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat/unsafe",
        runtime_type="langchain",
        runtime_session_id="runtime",
        runtime_root=root,
    )
    turn_request = RuntimeTurnRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat/unsafe",
        turn_id="turn",
        runtime_type="langchain",
        runtime_session_id="runtime",
        runtime_root=root,
        message={"role": "user", "content": "hello"},
        command_context={"is_first": True},
    )

    events = [
        event
        async for event in orchestrator.stream_turn(
            open_request=open_request,
            turn_request=turn_request,
            workspace_scope_id="workspace",
            current_workflow_id=None,
            stop_event=asyncio.Event(),
        )
    ]

    assert root == "/runtime/langchain/chats/chat_unsafe"
    assert [event[1]["phase"] for event in events[:4]] == [
        "acquiring_sandbox",
        "initializing_runtime",
        "connecting_model",
        "awaiting_first_output",
    ]
    assert events[4:] == [
        ("CHAT_EVENT", {"type": "message_replace", "content": "hello"})
    ]
    for _, payload in events[:4]:
        assert payload["first_turn"] is True
        assert payload["runtime_type"] == "langchain"
        assert payload["operation_id"] == "turn"
        assert payload["started_at"].endswith("+00:00")
    assert manager.calls[0][1]["lease"] == "resident"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("is_first", "runtime_state_ref"),
    [(True, None), (False, "existing-runtime-state")],
)
async def test_runtime_timing_separates_application_setup_from_model_wait(
    monkeypatch,
    is_first,
    runtime_state_ref,
):
    class TimingSandbox(_Sandbox):
        async def run_agent_runtime_stream(self, request):
            common = {
                "chat_id": request["chat_id"],
                "turn_id": request["turn_id"],
                "runtime_type": "langchain",
                "runtime_session_id": request["runtime_session_id"],
            }
            yield {
                **common,
                "event_id": "start",
                "seq": 1,
                "type": "runtime.started",
                "payload": {
                    "first_turn": not is_first,
                    "timings_ms": {
                        "setup_total_ms": 7,
                        "non_numeric_private_context": "must-not-be-logged",
                    },
                },
            }
            yield {
                **common,
                "event_id": "message-start",
                "seq": 2,
                "type": "message.start",
                "payload": {"message_id": "message-1", "role": "assistant"},
            }
            yield {
                **common,
                "event_id": "message-delta",
                "seq": 3,
                "type": "message.delta",
                "payload": {"message_id": "message-1", "delta": "private text"},
            }
            yield {
                **common,
                "event_id": "done",
                "seq": 4,
                "type": "runtime.completed",
            }

    class TimingLogger:
        def __init__(self):
            self.records = []

        def info(self, event, **values):
            self.records.append({"event": event, **values})

    manager = _Manager()
    manager.session = TimingSandbox()
    timing_logger = TimingLogger()
    monkeypatch.setattr(
        "vibecanvas_api.services.agent_runtime.orchestrator.logger",
        timing_logger,
    )
    root = private_runtime_root(RuntimeType.LANGCHAIN, "timing-chat")
    orchestrator = AgentRuntimeOrchestrator(manager)
    request = RuntimeTurnRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="timing-chat",
        turn_id="timing-turn",
        runtime_type="langchain",
        runtime_session_id="runtime",
        runtime_root=root,
        runtime_state_ref=runtime_state_ref,
        message={"role": "user", "content": "private prompt"},
        command_context={"is_first": is_first},
    )
    events = [
        item
        async for item in orchestrator.stream_turn(
            open_request=RuntimeOpenRequest(
                tenant_id="tenant",
                user_id="user",
                chat_id="timing-chat",
                runtime_type="langchain",
                runtime_session_id="runtime",
                runtime_root=root,
            ),
            turn_request=request,
            workspace_scope_id="workspace",
            current_workflow_id=None,
            stop_event=asyncio.Event(),
        )
    ]

    assert [item[1]["type"] for item in events[-2:]] == [
        "message_start",
        "message_delta",
    ]
    setup = next(
        record
        for record in timing_logger.records
        if record["event"] == "codex_runtime_setup_timing"
    )
    assert setup["first_turn"] is is_first
    assert setup["timing_setup_total_ms"] == 7
    assert "non_numeric_private_context" not in setup
    first_event = next(
        record
        for record in timing_logger.records
        if record.get("phase") == "first_product_event"
    )
    first_text = next(
        record
        for record in timing_logger.records
        if record.get("phase") == "first_model_text_delta"
    )
    assert first_event["first_turn"] is is_first
    assert first_event["application_setup_ms"] >= 0
    assert first_event["model_first_event_ms"] >= 0
    assert first_text["first_turn"] is is_first
    assert first_text["application_setup_ms"] >= 0
    assert first_text["model_ttft_ms"] >= 0
    assert "private prompt" not in str(timing_logger.records)
    assert "private text" not in str(timing_logger.records)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_mode", "tool_name", "arguments", "expected_action"),
    [
        (
            "always_allow",
            "browser_click",
            {"handle": "submit", "require_user_auth": True},
            "approve",
        ),
        (
            "agent",
            "browser_click",
            {"handle": "submit", "require_user_auth": False},
            "approve",
        ),
        ("agent", "unknown_mutation", {}, "deny"),
    ],
)
async def test_host_policy_resolves_without_hitl_before_runtime_continues(
    approval_mode,
    tool_name,
    arguments,
    expected_action,
):
    class CandidateSandbox(_Sandbox):
        def __init__(self):
            super().__init__()
            self.decision = asyncio.Event()

        async def run_agent_runtime_stream(self, request):
            common = {
                "chat_id": request["chat_id"],
                "turn_id": request["turn_id"],
                "runtime_type": "langchain",
                "runtime_session_id": request["runtime_session_id"],
            }
            yield {
                **common,
                "event_id": "approval-candidate",
                "seq": 1,
                "type": "approval.requested",
                "payload": {
                    "hitl_request_id": "hitl_immediate",
                    "title": f"Approve {tool_name}",
                    "prompt_text": "Review",
                    "agent_payload": {
                        "tool": tool_name,
                        "arguments": arguments,
                    },
                    "policy": {
                        "phase": "pre_tool",
                        "native_required": False,
                    },
                    "runtime_correlation": {
                        "source": "langchain",
                        "runtime_request_id": "tool-call",
                        "runtime_method": "tool/approval",
                    },
                },
            }
            await asyncio.wait_for(self.decision.wait(), timeout=2)
            yield {
                **common,
                "event_id": "completed",
                "seq": 2,
                "type": "runtime.completed",
            }

        async def send_agent_runtime_control(self, turn_id, response):
            await super().send_agent_runtime_control(turn_id, response)
            self.decision.set()

    manager = _Manager()
    manager.session = CandidateSandbox()
    orchestrator = AgentRuntimeOrchestrator(manager)
    open_request = RuntimeOpenRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat",
        runtime_type="langchain",
        runtime_session_id="runtime",
        runtime_root="/runtime/langchain/chats/chat",
    )
    turn_request = RuntimeTurnRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat",
        turn_id="turn",
        runtime_type="langchain",
        runtime_session_id="runtime",
        runtime_root="/runtime/langchain/chats/chat",
        message={"role": "user", "content": "do it"},
        approval_mode=approval_mode,
    )

    events = [
        item
        async for item in orchestrator.stream_turn(
            open_request=open_request,
            turn_request=turn_request,
            workspace_scope_id="workspace",
            current_workflow_id=None,
            stop_event=asyncio.Event(),
        )
    ]

    assert all(event_type == "RUNTIME_STATUS" for event_type, _ in events)
    assert len(manager.session.controls) == 1
    control = manager.session.controls[0][1]
    assert control["action"] == expected_action
    assert control["persisted"] is False


def test_codex_runtime_root_is_platform_private():
    assert private_runtime_root(RuntimeType.CODEX, "ignored") == "/runtime/.codex"


@pytest.mark.asyncio
async def test_codex_state_delete_removes_only_the_chat_scoped_directory(
    monkeypatch,
    tmp_path,
):
    from vibecanvas_api.config import config
    from vibecanvas_api.services.chat_workspace import chat_workspace_scope_id
    from vibecanvas_api.services.object_store import FilesystemObjectStore
    from vibecanvas_api.services import vfs_volume

    monkeypatch.setattr(config, "agent_runtime_root", str(tmp_path))
    monkeypatch.setattr(config, "vfs_volume_root", str(tmp_path))
    store = FilesystemObjectStore(
        root=str(tmp_path / "cipher"),
        materialized_root=str(tmp_path / "materialized"),
        master_key=b"D" * 32,
    )
    monkeypatch.setattr(vfs_volume, "get_object_store", lambda: store)
    chat_id = "chat-delete-runtime-state"
    provider = vfs_volume.get_chat_runtime_volume_provider()
    state_volume = provider.ensure(
        tenant_id="tenant",
        user_id="user",
        chat_scope_id=chat_workspace_scope_id(chat_id),
    )
    state_dir = state_volume.path
    with open(os.path.join(state_dir, "thread.jsonl"), "w", encoding="utf-8") as stream:
        stream.write("private thread state")
    provider.sync(state_volume)
    sibling_volume = provider.ensure(
        tenant_id="tenant",
        user_id="user",
        chat_scope_id=chat_workspace_scope_id("other-chat"),
    )
    sibling = sibling_volume.path
    with open(os.path.join(sibling, "thread.jsonl"), "w", encoding="utf-8") as stream:
        stream.write("sibling")
    provider.sync(sibling_volume)

    deleted = await AgentRuntimeOrchestrator(_Manager()).delete_state(
        RuntimeOpenRequest(
            tenant_id="tenant",
            user_id="user",
            chat_id=chat_id,
            runtime_type="codex",
            runtime_session_id="runtime",
            runtime_root="/runtime/.codex",
            state_ref="codex-thread",
        )
    )

    assert deleted is True
    assert not os.path.exists(state_dir)
    assert os.path.isdir(sibling)


@pytest.mark.asyncio
async def test_langchain_state_delete_uses_runtime_owned_store():
    store = _RuntimeStateStore()
    orchestrator = AgentRuntimeOrchestrator(_Manager(), store)
    deleted = await orchestrator.delete_state(
        RuntimeOpenRequest(
            tenant_id="tenant",
            user_id="user",
            chat_id="chat",
            runtime_type="langchain",
            runtime_session_id="runtime",
            runtime_root="/runtime/langchain/chats/chat",
            state_ref="langchain-thread",
        )
    )

    assert deleted is True
    assert store.deleted == ["langchain-thread"]


@pytest.mark.asyncio
async def test_orchestrator_delivers_durable_hitl_control_without_creating_session():
    from vibecanvas_api.services.agent_runtime.protocol import RuntimeControlResponse

    manager = _Manager()
    orchestrator = AgentRuntimeOrchestrator(manager)
    open_request = RuntimeOpenRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat",
        runtime_type="langchain",
        runtime_session_id="runtime",
        runtime_root="/runtime/langchain/chats/chat",
    )
    response = RuntimeControlResponse(
        request_id="hitl",
        chat_id="chat",
        turn_id="turn",
        gate_type="pre_tool_approval",
        action="approve",
        correlation={
            "source": "langchain",
            "runtime_request_id": "tool-call",
            "runtime_method": "tool/approval",
        },
    )
    await orchestrator.respond(
        open_request=open_request,
        response=response,
        workspace_scope_id="workspace",
        current_workflow_id=None,
    )

    assert manager.calls == [(('tenant', 'workspace'), {"loaded_only": True})]
    assert manager.session.controls[0][1]["request_id"] == "hitl"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("approval_source", "runtime_request_id", "runtime_method"),
    [
        (
            "codex_app_server",
            7,
            "item/commandExecution/requestApproval",
        ),
        (
            "platform_mcp",
            "browser-gateway-call-7",
            "tools/call",
        ),
    ],
)
async def test_codex_approval_and_thread_are_durable_cross_worker_rendezvous(
    pg_engine,
    approval_source: str,
    runtime_request_id: str | int,
    runtime_method: str,
):
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    chat_id = f"codex_orchestrator_{uuid.uuid4().hex[:8]}"
    turn_id = f"turn_{uuid.uuid4().hex}"
    async with pg_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:tenant, 'runtime')"),
            {"tenant": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:user, :tenant, :email)"
            ),
            {
                "user": user_id,
                "tenant": tenant_id,
                "email": f"codex-{uuid.uuid4().hex[:8]}@example.test",
            },
        )
    async with session_scope(tenant_id=tenant_id) as session:
        await ChatRepo(session, user_id).register_session(
            "scope",
            name="Codex",
            chat_id=chat_id,
            surface="chat",
        )
        binding = await AgentRuntimeRepo(session, user_id).bind_chat(
            chat_id, runtime_type="codex"
        )
        assert binding is not None
        await AgentRunsRepo(session).create_exclusive(
            run_id=turn_id,
            tenant_id=tenant_id,
            chat_id=chat_id,
            creator_user_id=user_id,
            client_request_id=f"request-{uuid.uuid4().hex}",
            input_message_id=f"message-{uuid.uuid4().hex}",
            input_snapshot={},
        )

    class ApprovalSandbox(_Sandbox):
        def __init__(self):
            super().__init__()
            self.decision = asyncio.Event()

        async def run_agent_runtime_stream(self, request):
            common = {
                "chat_id": request["chat_id"],
                "turn_id": request["turn_id"],
                "runtime_type": "codex",
                "runtime_session_id": request["runtime_session_id"],
            }
            yield {**common, "event_id": "start", "seq": 1, "type": "runtime.started"}
            yield {
                **common,
                "event_id": "checkpoint",
                "seq": 2,
                "type": "checkpoint",
                "payload": {"state_ref": "codex-thread-1"},
            }
            yield {
                **common,
                "event_id": "approval",
                "seq": 3,
                "type": "approval.requested",
                "payload": {
                    "hitl_request_id": "hitl_codex_test",
                    "hitl_type": "pre_tool_approval",
                    "title": "Approve command",
                    "prompt_text": "Run tests?",
                    "actions": [
                        {"id": "approve", "label": "Approve", "variant": "primary"},
                        {"id": "deny", "label": "Deny", "variant": "secondary"},
                    ],
                    "agent_payload": {
                        "tool": (
                            "browser_click"
                            if approval_source == "platform_mcp"
                            else "shell"
                        ),
                        "arguments": (
                            {"handle": "submit", "require_user_auth": True}
                            if approval_source == "platform_mcp"
                            else {"command": "pytest"}
                        ),
                    },
                    "policy": {
                        "phase": "pre_tool",
                        "native_required": (
                            approval_source == "codex_app_server"
                        ),
                    },
                    "runtime_correlation": {
                        "source": approval_source,
                        "runtime_request_id": runtime_request_id,
                        "runtime_method": runtime_method,
                        "runtime_thread_id": "codex-thread-1",
                        "runtime_turn_id": "codex-turn-1",
                        "runtime_item_id": "item-1",
                    },
                },
            }
            await asyncio.wait_for(self.decision.wait(), timeout=5)
            yield {**common, "event_id": "done", "seq": 4, "type": "runtime.completed"}

        async def send_agent_runtime_control(self, turn_id_value, response):
            await super().send_agent_runtime_control(turn_id_value, response)
            self.decision.set()

    manager = _Manager()
    manager.session = ApprovalSandbox()
    root = private_runtime_root(RuntimeType.CODEX, chat_id)
    open_request = RuntimeOpenRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        chat_id=chat_id,
        runtime_type="codex",
        runtime_session_id=binding["runtime_session_id"],
        runtime_root=root,
    )
    turn_request = RuntimeTurnRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        chat_id=chat_id,
        turn_id=turn_id,
        runtime_type="codex",
        runtime_session_id=binding["runtime_session_id"],
        runtime_root=root,
        message={"role": "user", "content": "test"},
        model={
            "id": "gpt-test",
            "base_url": "http://platform.test/api/internal/runtime-model/v1",
            "api_key": "turn-capability",
        },
    )

    collect = asyncio.create_task(_collect_runtime_events(
        AgentRuntimeOrchestrator(manager), open_request, turn_request
    ))
    for _ in range(30):
        async with session_scope(tenant_id=tenant_id) as session:
            row = await HitlRepo(session).get_request("hitl_codex_test")
            if row is not None:
                break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("approval was not persisted")

    async with session_scope(tenant_id=tenant_id) as session:
        row, applied = await HitlRepo(session).resolve(
            hitl_request_id="hitl_codex_test",
            decision="approve",
            decision_payload={},
        )
        assert row is not None and applied

    events = await asyncio.wait_for(collect, timeout=5)
    assert any(event_type == "HITL_REQUIRED" for event_type, _ in events)
    assert (
        manager.session.controls[0][1]["correlation"]["runtime_request_id"]
        == runtime_request_id
    )
    assert manager.session.controls[0][1]["persisted"] is True
    async with session_scope(tenant_id=tenant_id) as session:
        saved = await AgentRuntimeRepo(session, user_id).get_chat_binding(chat_id)
        assert saved is not None
        assert saved["runtime_state_ref"] == "codex-thread-1"


@pytest.mark.asyncio
async def test_stop_interrupts_runtime_suspended_on_hitl_and_freezes_request(
    pg_engine,
):
    tenant_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    chat_id = f"codex_stop_{uuid.uuid4().hex[:8]}"
    turn_id = f"turn_{uuid.uuid4().hex}"
    hitl_request_id = f"hitl_{uuid.uuid4().hex[:16]}"
    async with pg_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:tenant, 'runtime')"),
            {"tenant": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:user, :tenant, :email)"
            ),
            {
                "user": user_id,
                "tenant": tenant_id,
                "email": f"codex-stop-{uuid.uuid4().hex[:8]}@example.test",
            },
        )
    async with session_scope(tenant_id=tenant_id) as session:
        await ChatRepo(session, user_id).register_session(
            "scope",
            name="Codex stop",
            chat_id=chat_id,
            surface="chat",
        )
        binding = await AgentRuntimeRepo(session, user_id).bind_chat(
            chat_id, runtime_type="codex"
        )
        assert binding is not None
        await AgentRunsRepo(session).create_exclusive(
            run_id=turn_id,
            tenant_id=tenant_id,
            chat_id=chat_id,
            creator_user_id=user_id,
            client_request_id=f"request-{uuid.uuid4().hex}",
            input_message_id=f"message-{uuid.uuid4().hex}",
            input_snapshot={},
        )

    class SuspendedApprovalSandbox(_Sandbox):
        def __init__(self):
            super().__init__()
            self.cancelled_turns: list[str] = []
            self.closed = asyncio.Event()
            self.never_resolved = asyncio.Event()

        async def run_agent_runtime_stream(self, request):
            common = {
                "chat_id": request["chat_id"],
                "turn_id": request["turn_id"],
                "runtime_type": "codex",
                "runtime_session_id": request["runtime_session_id"],
            }
            yield {**common, "event_id": "start", "seq": 1, "type": "runtime.started"}
            yield {
                **common,
                "event_id": "approval",
                "seq": 2,
                "type": "approval.requested",
                "payload": {
                    "hitl_request_id": hitl_request_id,
                    "hitl_type": "pre_tool_approval",
                    "title": "Approve browser click",
                    "prompt_text": "Allow this click?",
                    "actions": [
                        {"id": "approve", "label": "Approve", "variant": "primary"},
                        {"id": "deny", "label": "Deny", "variant": "secondary"},
                    ],
                    "agent_payload": {
                        "tool": "browser_click",
                        "arguments": {
                            "handle": "submit",
                            "require_user_auth": True,
                        },
                    },
                    "policy": {
                        "phase": "pre_tool",
                        "native_required": False,
                    },
                    "runtime_correlation": {
                        "source": "platform_mcp",
                        "runtime_request_id": "gateway-call",
                        "runtime_method": "tools/call",
                        "runtime_item_id": "browser-item",
                    },
                },
            }
            try:
                await self.never_resolved.wait()
            finally:
                self.closed.set()

        async def cancel_agent_runtime(self, value):
            self.cancelled_turns.append(value)
            return True

    manager = _Manager()
    manager.session = SuspendedApprovalSandbox()
    root = private_runtime_root(RuntimeType.CODEX, chat_id)
    open_request = RuntimeOpenRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        chat_id=chat_id,
        runtime_type="codex",
        runtime_session_id=binding["runtime_session_id"],
        runtime_root=root,
    )
    turn_request = RuntimeTurnRequest(
        tenant_id=tenant_id,
        user_id=user_id,
        chat_id=chat_id,
        turn_id=turn_id,
        runtime_type="codex",
        runtime_session_id=binding["runtime_session_id"],
        runtime_root=root,
        message={"role": "user", "content": "click it"},
        model={
            "id": "gpt-test",
            "base_url": "http://platform.test/api/internal/runtime-model/v1",
            "api_key": "turn-capability",
        },
    )
    stop_event = asyncio.Event()
    collect = asyncio.create_task(
        _collect_runtime_events(
            AgentRuntimeOrchestrator(manager),
            open_request,
            turn_request,
            stop_event=stop_event,
        )
    )

    for _ in range(40):
        async with session_scope(tenant_id=tenant_id) as session:
            pending = await HitlRepo(session).get_request(hitl_request_id)
            if pending is not None:
                break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("approval was not persisted")

    async with session_scope(tenant_id=tenant_id) as session:
        requested = await AgentRunsRepo(session).request_cancel(
            chat_id,
            turn_id,
            creator_user_id=user_id,
        )
        assert requested is True
    stop_event.set()

    events = await asyncio.wait_for(collect, timeout=2)
    assert any(event_type == "HITL_REQUIRED" for event_type, _ in events)
    assert manager.session.cancelled_turns == [turn_id]
    assert manager.session.closed.is_set()
    async with session_scope(tenant_id=tenant_id) as session:
        frozen = await HitlRepo(session).get_request(hitl_request_id)
        assert frozen is not None
        assert frozen.status == "cancelled"
        assert frozen.is_interacted is True
        run = await AgentRunsRepo(session).get(turn_id)
        assert run is not None
        assert run.status == "cancel_requested"


async def _collect_runtime_events(
    orchestrator,
    open_request,
    turn_request,
    *,
    stop_event=None,
):
    return [
        event
        async for event in orchestrator.stream_turn(
            open_request=open_request,
            turn_request=turn_request,
            workspace_scope_id="workspace",
            current_workflow_id=None,
            stop_event=stop_event or asyncio.Event(),
        )
    ]
