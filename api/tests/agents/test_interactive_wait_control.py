from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from vibecanvas_api.agent import (
    AgentContext,
    _bind_hitl_request_to_tool_projection,
    _interactive_artifact_id_from_message,
    _interactive_completion_mode,
    _stream_and_yield,
    _tool_message_waits_for_user,
)


def _interactive_tool_message(mode: str, *, inline: bool = True) -> ToolMessage:
    definition = {
        "kind": "interactive_artifact",
        "artifact_id": "ia_wait",
        "completion_mode": mode,
    }
    return ToolMessage(
        content=json.dumps({
            "status": "success",
            "output": {
                "artifact_id": "ia_wait",
                "completion_mode": mode,
            },
        }),
        name="render_interactive",
        tool_call_id="tc_wait",
        artifact={
            "status": "success",
            "payload": {"artifact": definition if inline else None},
        },
    )


def test_interactive_wait_detection_supports_inline_and_offloaded_artifacts():
    assert _interactive_completion_mode(
        _interactive_tool_message("wait_for_submit")
    ) == "wait_for_submit"
    assert _tool_message_waits_for_user(
        _interactive_tool_message("wait_for_submit", inline=False)
    )
    assert not _tool_message_waits_for_user(
        _interactive_tool_message("render_only")
    )


def test_interactive_wait_detection_normalizes_mcp_structured_content():
    definition = {
        "kind": "interactive_artifact",
        "artifact_id": "ia_mcp_wait",
        "completion_mode": "wait_for_submit",
        "interaction_state": {"status": "awaiting_loop_gate"},
    }
    message = ToolMessage(
        content='[{"type":"text","text":"rendered"}]',
        name="render_interactive",
        tool_call_id="tc_mcp_wait",
        artifact={
            "structured_content": {
                "schema_version": 1,
                "status": "success",
                "content": "rendered",
                "payload": {
                    "kind": "interactive_artifact",
                    "artifact": definition,
                },
                "meta": {"tool": "render_interactive"},
            },
        },
    )

    assert _interactive_completion_mode(message) == "wait_for_submit"
    assert _interactive_artifact_id_from_message(message) == "ia_mcp_wait"

    _bind_hitl_request_to_tool_projection(
        message,
        "hitl_mcp_wait",
        status="pending",
    )
    structured = message.artifact["structured_content"]
    assert structured["payload"]["hitl_request_id"] == "hitl_mcp_wait"
    assert structured["payload"]["artifact"]["hitl_request_id"] == "hitl_mcp_wait"
    assert structured["payload"]["artifact"]["interaction_state"]["status"] == "pending"


@pytest.mark.asyncio
async def test_outer_loop_stops_after_interactive_wait_tool_message(monkeypatch):
    # Persistence ordering is covered by the DB integration test; this unit
    # isolates the stream-control behavior.
    async def durable_gate(**_kwargs):
        return "hitl_wait"

    monkeypatch.setattr(
        "vibecanvas_api.agent._ensure_post_tool_interaction_request",
        durable_gate,
    )
    wait_message = _interactive_tool_message("wait_for_submit")
    sibling_message = ToolMessage(
        content="sibling completed",
        name="read_file",
        tool_call_id="tc_sibling",
    )
    forbidden_follow_up = AIMessage(content="This must not run before user input.")

    class FakeStream:
        def __init__(self):
            self._chunks = iter([
                {
                    "type": "updates",
                    "data": {"tools": {"messages": [wait_message, sibling_message]}},
                },
                {
                    "type": "updates",
                    "data": {"model": {"messages": [forbidden_follow_up]}},
                },
            ])
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._chunks)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def aclose(self):
            self.closed = True

    stream = FakeStream()

    class FakeAgent:
        def astream(self, *args, **kwargs):
            return stream

    def build_signal(kind: str, payload: dict) -> dict:
        return {"kind": kind, "payload": payload}

    context = AgentContext()
    events = [
        event
        async for event in _stream_and_yield(
            FakeAgent(),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread_wait"}},
            chat_id="chat_wait",
            build_signal=build_signal,
            context=context,
            emit_noop=True,
            turn_id="turn_wait",
        )
    ]

    assert stream.closed is True
    tool_end_events = [
        event
        for event in events
        if event["kind"] == "CHAT_EVENT"
        and event["payload"].get("type") == "tool_end"
    ]
    assert len(tool_end_events) == 2
    assert all(
        "This must not run before user input." not in json.dumps(event)
        for event in events
    )
