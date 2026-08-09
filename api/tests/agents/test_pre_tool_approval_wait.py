from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import vibecanvas_api.agent as agent_mod
from vibecanvas_api.agents.middleware.user_approval import UserApprovalMiddleware


@pytest.mark.asyncio
async def test_langchain_loop_suspends_through_runtime_callback_before_tool_update():
    tool_call = {
        "id": "tc_approval",
        "name": "browser_click",
        "args": {"handle": "h1", "require_user_auth": True},
    }
    ai = AIMessage(content="", tool_calls=[tool_call])
    tool_result = ToolMessage(
        content="clicked",
        tool_call_id="tc_approval",
        name="browser_click",
    )
    approval_finished = False

    class FakeStream:
        def __init__(self):
            self.items = iter([
                {"type": "updates", "data": {"model": {"messages": [ai]}}},
                {"type": "updates", "data": {"tools": {"messages": [tool_result]}}},
            ])

        async def __anext__(self):
            try:
                item = next(self.items)
            except StopIteration as exc:
                raise StopAsyncIteration from exc
            if "tools" in item["data"]:
                assert approval_finished is True
            return item

    class FakeAgent:
        def astream(self, *args, **kwargs):
            return FakeStream()

    calls: list[tuple[str, str, dict]] = []

    async def request_approval(tool_name, tool_call_id, arguments):
        nonlocal approval_finished
        calls.append((tool_name, tool_call_id, arguments))
        approval_finished = True
        return "approved"

    context = agent_mod.AgentContext(
        tenant_id="00000000-0000-0000-0000-000000000001",
        chat_id="chat_approval",
        turn_id="turn_approval",
        approval_mode="agent",
    )

    def build_signal(event_type, payload):
        return {"type": event_type, "payload": payload}

    events = [
        event
        async for event in agent_mod._stream_and_yield(
            FakeAgent(),
            input_data={"messages": []},
            config={"configurable": {"thread_id": "thread_approval"}},
            chat_id="chat_approval",
            build_signal=build_signal,
            context=context,
            turn_id="turn_approval",
            request_tool_approval=request_approval,
        )
    ]

    assert calls == [(
        "browser_click",
        "tc_approval",
        {"handle": "h1", "require_user_auth": True},
    )]
    assert context.tool_approval_decisions == {"tc_approval": "approved"}
    assert not any(event["type"] == "HITL_REQUIRED" for event in events)


def _middleware_request(decision: str | None):
    decisions = {} if decision is None else {"tc": decision}
    return SimpleNamespace(
        tool_call={
            "id": "tc",
            "name": "browser_click",
            "args": {"handle": "submit"},
        },
        runtime=SimpleNamespace(
            context=SimpleNamespace(tool_approval_decisions=decisions),
        ),
    )


@pytest.mark.asyncio
async def test_denied_tool_call_never_enters_handler():
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return ToolMessage(content="executed", tool_call_id="tc")

    result = await UserApprovalMiddleware().awrap_tool_call(
        _middleware_request("denied"),
        handler,
    )

    assert calls == 0
    assert result.artifact["payload"]["not_executed"] is True


@pytest.mark.asyncio
async def test_missing_decision_fails_closed_without_entering_handler():
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return ToolMessage(content="executed", tool_call_id="tc")

    result = await UserApprovalMiddleware().awrap_tool_call(
        _middleware_request(None),
        handler,
    )

    assert calls == 0
    assert result.artifact["error"]["code"] == "approval_missing"


@pytest.mark.asyncio
async def test_approved_tool_call_enters_handler_exactly_once():
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return ToolMessage(content="executed", tool_call_id="tc")

    result = await UserApprovalMiddleware().awrap_tool_call(
        _middleware_request("approved"),
        handler,
    )

    assert calls == 1
    assert result.content == "executed"
