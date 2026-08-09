from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import vibecanvas_api.agent as agent_mod


class FakeStream:
    def __init__(self, items):
        self._items = iter(items)

    async def __anext__(self):
        try:
            return next(self._items)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeAgent:
    def __init__(self, items):
        self._items = items

    def astream(self, *_args, **_kwargs):
        return FakeStream(self._items)


def signal(kind: str, payload: dict) -> dict:
    return {"kind": kind, "payload": payload}


@pytest.mark.asyncio
async def test_langchain_text_chunks_are_projected_before_the_final_message():
    items = [
        {
            "type": "messages",
            "data": (AIMessageChunk(content="hel"), {}),
        },
        {
            "type": "messages",
            "data": (AIMessageChunk(content="lo"), {}),
        },
        {
            "type": "updates",
            "data": {"model": {"messages": [AIMessage(content="hello")]}},
        },
    ]

    events = [
        event["payload"]
        async for event in agent_mod._stream_and_yield(
            FakeAgent(items),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread"}},
            chat_id="chat",
            build_signal=signal,
            turn_id="turn",
        )
        if event["kind"] == "CHAT_EVENT"
    ]

    replacements = [
        event["content"]
        for event in events
        if event.get("type") == "message_replace"
    ]
    assert replacements[:2] == ["hel", "hello"]
    assert next(event for event in events if event["type"] == "message_end")


@pytest.mark.asyncio
async def test_streamed_ai_prefix_is_preserved_when_tool_call_arrives():
    tool_call = {
        "id": "tc_1",
        "name": "read_file",
        "args": {"path": "/data/a.txt"},
    }
    items = [
        {
            "type": "messages",
            "data": (AIMessageChunk(content="我先读取文件。"), {}),
        },
        {
            "type": "updates",
            "data": {
                "model": {
                    "messages": [
                        AIMessage(content="我先读取文件。", tool_calls=[tool_call])
                    ]
                }
            },
        },
    ]

    events = [
        event["payload"]
        async for event in agent_mod._stream_and_yield(
            FakeAgent(items),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread"}},
            chat_id="chat",
            build_signal=signal,
            turn_id="turn",
        )
        if event["kind"] == "CHAT_EVENT"
    ]

    replacements = [
        event["content"]
        for event in events
        if event.get("type") == "message_replace"
    ]
    assert replacements[-1] == "我先读取文件。"
    tool_start = next(event for event in events if event.get("type") == "tool_start")
    assert tool_start["message_id"] == next(
        event["message_id"]
        for event in events
        if event.get("type") == "message_start"
    )


@pytest.mark.asyncio
async def test_updates_only_tool_call_keeps_non_streamed_ai_content():
    tool_call = {"id": "tc_2", "name": "list_files", "args": {}}
    items = [{
        "type": "updates",
        "data": {
            "model": {
                "messages": [
                    AIMessage(content="接下来检查目录。", tool_calls=[tool_call])
                ]
            }
        },
    }]

    events = [
        event["payload"]
        async for event in agent_mod._stream_and_yield(
            FakeAgent(items),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread"}},
            chat_id="chat",
            build_signal=signal,
            turn_id="turn",
        )
        if event["kind"] == "CHAT_EVENT"
    ]

    assert events[0]["type"] == "message_start"
    assert events[0]["content"] == "接下来检查目录。"
    assert events[1]["type"] == "tool_start"
    assert events[2]["type"] == "message_end"


@pytest.mark.asyncio
async def test_empty_terminal_ai_message_fails_without_automatic_continuation():
    items = [{
        "type": "updates",
        "data": {"model": {"messages": [AIMessage(content=[])]}},
    }]

    with pytest.raises(
        agent_mod.EmptyModelResponseError,
        match="stopped without an automatic retry",
    ):
        async for _event in agent_mod._stream_and_yield(
            FakeAgent(items),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread"}},
            chat_id="chat",
            build_signal=signal,
            turn_id="turn",
        ):
            pass


@pytest.mark.asyncio
async def test_stream_preserves_exact_ai_tool_ai_semantic_order():
    tool_call = {
        "id": "tc_order",
        "name": "read_file",
        "args": {"path": "/data/a.txt"},
    }
    items = [
        {
            "type": "messages",
            "data": (AIMessageChunk(content="我先读取。"), {}),
        },
        {
            "type": "updates",
            "data": {
                "model": {
                    "messages": [
                        AIMessage(content="我先读取。", tool_calls=[tool_call])
                    ]
                }
            },
        },
        {
            "type": "updates",
            "data": {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content="file contents",
                            tool_call_id="tc_order",
                            name="read_file",
                        )
                    ]
                }
            },
        },
        {
            "type": "messages",
            "data": (AIMessageChunk(content="读取完成。"), {}),
        },
        {
            "type": "updates",
            "data": {"model": {"messages": [AIMessage(content="读取完成。")]},},
        },
    ]

    events = [
        event["payload"]
        async for event in agent_mod._stream_and_yield(
            FakeAgent(items),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread"}},
            chat_id="chat",
            build_signal=signal,
            turn_id="turn",
        )
        if event["kind"] == "CHAT_EVENT"
    ]

    boundary_types = [
        event["type"]
        for event in events
        if event["type"] != "message_replace"
    ]
    assert boundary_types == [
        "message_start",
        "tool_start",
        "message_end",
        "tool_end",
        "message_start",
        "message_end",
        # The defensive final flush repeats the same message_id so an SSE
        # reconnect can heal a dropped end frame without creating a new message.
        "message_end",
    ]
    starts = [event for event in events if event["type"] == "message_start"]
    ends = [event for event in events if event["type"] == "message_end"]
    assert len(starts) == 2
    assert starts[0]["message_id"] != starts[1]["message_id"]
    assert [event["message_id"] for event in ends] == [
        starts[0]["message_id"],
        starts[1]["message_id"],
        starts[1]["message_id"],
    ]
    replacements = [event for event in events if event["type"] == "message_replace"]
    assert [event["content"] for event in replacements] == [
        "我先读取。",
        "我先读取。",
        "读取完成。",
        "读取完成。",
    ]
    assert [event["message_id"] for event in replacements] == [
        starts[0]["message_id"],
        starts[0]["message_id"],
        starts[1]["message_id"],
        starts[1]["message_id"],
    ]
    assert sum(event["type"] == "tool_start" for event in events) == 1
    assert sum(event["type"] == "tool_end" for event in events) == 1
