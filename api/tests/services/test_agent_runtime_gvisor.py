from __future__ import annotations

import uuid

import pytest

from vibecanvas_api.services.sandbox import _gvisor_runnable, _resolve_runsc
from vibecanvas_api.services.sandbox.gvisor import (
    RootlessGvisorProvider,
    _workflow_python_binds,
)
from vibecanvas_api.services.sandbox.manager import SandboxSession


pytestmark = [
    pytest.mark.gvisor,
    pytest.mark.skipif(not _gvisor_runnable(), reason="rootless gVisor unavailable"),
]


@pytest.mark.asyncio
async def test_langchain_runtime_stream_crosses_real_gvisor_bus(tmp_path, pg_engine):
    """Exercise the new launcher, UDS request, adapter entry, and event stream.

    Deliberately omit model credentials: the expected assistant init-error event
    proves the LangChain process reached its normal error projection without an
    external model call.
    """
    run_dir = tmp_path / "workspace"
    for folder in ("data", "memory", "logs"):
        (run_dir / folder).mkdir(parents=True, exist_ok=True)
    overlay = tmp_path / "overlay"
    (overlay / "py").mkdir(parents=True)
    session = SandboxSession(
        tenant_id=str(uuid.uuid4()),
        wf_id="runtime_gvisor_workspace",
        run_dir=str(run_dir),
        overlay_dir=str(overlay),
        provider=RootlessGvisorProvider(_resolve_runsc()),
        base_binds=_workflow_python_binds(),
        # LangChain owns PostgreSQL checkpoint state and must not receive the
        # private filesystem mount reserved for file-backed Runtimes.
        runtime_dir=None,
        expose_run=False,
    )
    turn_id = f"turn_{uuid.uuid4().hex}"
    request = {
        "tenant_id": session.tenant_id,
        "user_id": str(uuid.uuid4()),
        "chat_id": "runtime_gvisor_chat",
        "turn_id": turn_id,
        "runtime_type": "langchain",
        "runtime_session_id": f"rt_langchain_{uuid.uuid4().hex}",
        "runtime_root": "/runtime/langchain/chats/runtime_gvisor_chat",
        "runtime_state_ref": f"runtime-gvisor-{uuid.uuid4().hex}",
        "message": {"role": "user", "content": "hello"},
        "model": {"model": "openai:gpt-4.1"},
        "surface": "main",
        "command_context": {
            "workspace_scope_id": "runtime_gvisor_workspace",
            "available_commands": [],
            "active_modes": [],
            "agent_surface": "chat",
        },
    }

    try:
        events = [event async for event in session.run_agent_runtime_stream(request)]
        types = [event["type"] for event in events]
        assert types[0] == "runtime.started"
        assert "projection" in types
        assert types[-1] == "runtime.completed"
        assert session._runtime_handle is not None
        assert session._runtime_handle.proc.poll() is None
        warm_process_pid = session._runtime_handle.proc.pid
        assert any(
            event["payload"].get("event_type") == "CHAT_EVENT"
            and event["payload"].get("payload", {}).get("type") == "message_replace"
            for event in events
        )

        next_request = {
            **request,
            "turn_id": f"turn_{uuid.uuid4().hex}",
            "message": {"role": "user", "content": "hello again"},
            "command_context": {
                **request["command_context"],
                "is_first": False,
            },
        }
        next_events = [
            event
            async for event in session.run_agent_runtime_stream(next_request)
        ]
        assert next_events[0]["type"] == "runtime.started"
        assert next_events[-1]["type"] == "runtime.completed"
        assert session._runtime_handle.proc.pid == warm_process_pid
    finally:
        await session.close()
