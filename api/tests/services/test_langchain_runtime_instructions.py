from __future__ import annotations

from vibecanvas_api.services.agent_runtime.protocol import RuntimeTurnRequest
from vibecanvas_api.services.agent_runtime.sandbox_entry import (
    _command_instruction_projection,
)


def _request(*, state_ref: str | None, activated: bool) -> RuntimeTurnRequest:
    return RuntimeTurnRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat",
        turn_id="turn",
        runtime_type="langchain",
        runtime_session_id="session",
        runtime_root="/runtime/langchain/chats/chat",
        runtime_state_ref=state_ref,
        message={"role": "user", "content": "build it"},
        command_context={
            "active_modes": ["build"],
            "activated_this_turn": ["build"] if activated else [],
        },
        instructions=[{
            "instruction_id": "command:build:v1",
            "kind": "command_context",
            "scope": "chat",
            "name": "build",
            "version": 1,
            "content": "BACKEND-RESOLVED BUILD CONTEXT",
            "activated_this_turn": activated,
        }],
    )


def test_langchain_projects_backend_instruction_without_command_registry() -> None:
    contexts, activated = _command_instruction_projection(
        _request(state_ref="checkpoint-thread", activated=True)
    )
    assert contexts == {"build": "BACKEND-RESOLVED BUILD CONTEXT"}
    assert activated == {"build"}


def test_langchain_seeds_sticky_instructions_when_native_state_is_missing() -> None:
    contexts, activated = _command_instruction_projection(
        _request(state_ref=None, activated=False)
    )
    assert contexts == {"build": "BACKEND-RESOLVED BUILD CONTEXT"}
    assert activated == {"build"}
