from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from vibecanvas_api.agents.middleware.diagram_protocol import (
    DiagramProtocolMiddleware,
)


def _tool(action: str, *, call_id: str = "diagram-call") -> ToolMessage:
    return ToolMessage(
        name="review_diagram",
        tool_call_id=call_id,
        content=json.dumps({"next": {"action": action}}),
    )


@pytest.mark.asyncio
async def test_forces_current_turn_diagram_chain_until_deliver() -> None:
    middleware = DiagramProtocolMiddleware(max_forced_continuations=3)
    tool_call = AIMessage(
        content="",
        tool_calls=[{"id": "diagram-call", "name": "review_diagram", "args": {}}],
    )
    assert await middleware.aafter_model({"messages": [tool_call]}, None) is None

    update = await middleware.aafter_model(
        {"messages": [tool_call, _tool("edit_source"), AIMessage(content="done")]},
        None,
    )
    assert update is not None and update["jump_to"] == "model"
    assert "next.action=edit_source" in update["messages"][0].content

    delivered = await middleware.aafter_model(
        {"messages": [tool_call, _tool("deliver"), AIMessage(content="done")]},
        None,
    )
    assert delivered is None


@pytest.mark.asyncio
async def test_ignores_prior_turn_and_non_diagram_tools() -> None:
    middleware = DiagramProtocolMiddleware()
    prior = _tool("edit_source", call_id="prior-call")
    assert await middleware.aafter_model(
        {"messages": [prior, AIMessage(content="ordinary answer")]},
        None,
    ) is None


@pytest.mark.asyncio
async def test_bounds_forced_continuations() -> None:
    middleware = DiagramProtocolMiddleware(max_forced_continuations=2)
    tool_call = AIMessage(
        content="",
        tool_calls=[{"id": "diagram-call", "name": "review_diagram", "args": {}}],
    )
    await middleware.aafter_model({"messages": [tool_call]}, None)
    state = {"messages": [tool_call, _tool("edit_source"), AIMessage(content="done")]}
    assert await middleware.aafter_model(state, None) is not None
    assert await middleware.aafter_model(state, None) is not None
    assert await middleware.aafter_model(state, None) is None


@pytest.mark.asyncio
async def test_continuation_budget_resets_for_each_product_turn() -> None:
    middleware = DiagramProtocolMiddleware(max_forced_continuations=1)
    tool_call = AIMessage(
        content="",
        tool_calls=[{"id": "diagram-call", "name": "review_diagram", "args": {}}],
    )
    state = {"messages": [tool_call, _tool("edit_source"), AIMessage(content="done")]}
    first_runtime = SimpleNamespace(
        context=SimpleNamespace(turn_id="turn-one"),
    )
    second_runtime = SimpleNamespace(
        context=SimpleNamespace(turn_id="turn-two"),
    )

    assert await middleware.aafter_model(
        {"messages": [tool_call]}, first_runtime
    ) is None
    assert await middleware.aafter_model(state, first_runtime) is not None
    assert await middleware.aafter_model(state, first_runtime) is None
    assert await middleware.aafter_model(
        {"messages": [tool_call]}, second_runtime
    ) is None
    assert await middleware.aafter_model(state, second_runtime) is not None
