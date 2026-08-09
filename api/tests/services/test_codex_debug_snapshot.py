from __future__ import annotations

import json

from vibecanvas_api.services.agent_runtime import codex_debug_snapshot as snapshot_module
from vibecanvas_api.services.agent_runtime.codex_debug_snapshot import (
    build_codex_debug_snapshot,
    write_codex_debug_snapshot,
)
from vibecanvas_api.services.agent_runtime.protocol import RuntimeTurnRequest


def _request() -> RuntimeTurnRequest:
    return RuntimeTurnRequest(
        tenant_id="tenant",
        user_id="user",
        chat_id="chat/unsafe",
        turn_id="turn/2",
        runtime_type="codex",
        runtime_session_id="runtime-session",
        runtime_root="/runtime/.codex",
        runtime_state_ref="codex-thread",
        message={"role": "user", "content": "current question"},
        model={
            "id": "gpt-codex-current",
            "base_url": "http://platform.test/api/internal/runtime-model/v1",
            "api_key": "turn-capability",
        },
        reasoning_effort="high",
    )


def test_codex_snapshot_projects_native_thread_and_current_turn_input() -> None:
    request = _request()
    thread = {
        "id": "codex-thread",
        "modelProvider": "openai",
        "turns": [
            {
                "id": "native-turn-1",
                "itemsView": "full",
                "items": [
                    {
                        "id": "user-1",
                        "type": "userMessage",
                        "content": [{"type": "text", "text": "earlier question"}],
                    },
                    {
                        "id": "reasoning-1",
                        "type": "reasoning",
                        "summary": ["Checked the relevant files."],
                        "content": ["private hidden reasoning"],
                    },
                    {
                        "id": "command-1",
                        "type": "commandExecution",
                        "command": "rg foo",
                        "cwd": "/data",
                        "status": "completed",
                        "aggregatedOutput": "match",
                    },
                    {
                        "id": "assistant-1",
                        "type": "agentMessage",
                        "text": "Earlier answer",
                        "phase": "final_answer",
                    },
                ],
            }
        ],
    }
    current_input = [{
        "type": "text",
        "text": (
            "<resolved-command-context>backend context</resolved-command-context>\n"
            "<user-message>current question</user-message>"
        ),
    }]

    snapshot = build_codex_debug_snapshot(
        request=request,
        thread=thread,
        thread_id="codex-thread",
        current_input=current_input,
    )

    assert snapshot["runtime_type"] == "codex"
    assert snapshot["snapshot_semantics"] == "runtime_thread_input"
    assert snapshot["target"]["provider"] == "openai"
    assert snapshot["target"]["model_id"] == "gpt-codex-current"
    assert snapshot["token_total"] is None
    assert snapshot["runtime_metadata"]["history_complete"] is True
    assert [item["runtime_item_type"] for item in snapshot["messages"]] == [
        "userMessage",
        "reasoning",
        "commandExecution",
        "agentMessage",
        "turnInput",
    ]
    assert snapshot["messages"][1]["content"] == "Checked the relevant files."
    assert snapshot["messages"][1]["runtime_metadata"]["has_hidden_content"] is True
    assert "private hidden reasoning" not in json.dumps(snapshot)
    assert "backend context" in snapshot["messages"][-1]["content"]
    assert snapshot["messages"][-1]["runtime_metadata"]["current_turn"] is True
    assert "/" not in snapshot["snapshot_id"]


def test_codex_snapshot_marks_summarized_native_history() -> None:
    snapshot = build_codex_debug_snapshot(
        request=_request(),
        thread={
            "id": "codex-thread",
            "turns": [{
                "id": "native-turn-1",
                "itemsView": "summary",
                "items": [],
            }],
        },
        thread_id="codex-thread",
        current_input=[{"type": "text", "text": "hello"}],
    )

    assert snapshot["runtime_metadata"]["history_complete"] is False


def test_codex_snapshot_write_is_atomic_and_debug_gated(
    monkeypatch, tmp_path
) -> None:
    debug_dir = tmp_path / "logs" / ".debug"
    monkeypatch.setattr(snapshot_module, "DEBUG_DIR", str(debug_dir))
    payload = build_codex_debug_snapshot(
        request=_request(),
        thread={"id": "codex-thread", "turns": []},
        thread_id="codex-thread",
        current_input=[{"type": "text", "text": "hello"}],
    )

    monkeypatch.delenv("AGENT_DEBUG_VIEW_ENABLED", raising=False)
    assert write_codex_debug_snapshot(payload) is None
    assert not debug_dir.exists()

    monkeypatch.setenv("AGENT_DEBUG_VIEW_ENABLED", "1")
    path = write_codex_debug_snapshot(payload)

    assert path is not None
    assert json.loads(debug_dir.joinpath(path.rsplit("/", 1)[-1]).read_text())[
        "runtime_type"
    ] == "codex"
    assert list(debug_dir.glob("*.tmp")) == []
