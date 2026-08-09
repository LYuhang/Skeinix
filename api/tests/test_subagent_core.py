# -*- coding: utf-8 -*-
"""Plan C C1 — SubAgentCore bounded runner.

These tests drive the REAL ``create_agent`` loop with a doubled chat model so
termination behaviour is actually exercised (not mocked away).

The model double subclasses langchain's ``GenericFakeChatModel`` and overrides
``bind_tools`` to return ``self`` — ``create_agent`` calls ``model.bind_tools(...)``
during build, and the stock fake raises ``NotImplementedError`` there. The fake
emits a scripted iterator of ``AIMessage``s; to drive the terminal ``set_output``
tool we hand it an ``AIMessage`` carrying ``tool_calls=[{"name":"set_output",
"args":{...}, ...}]``.

Because ``set_output`` is ``return_direct=True``, the agent loop STOPS the instant
it runs — test 1 asserts this by checking the SECOND scripted message is never
consumed (only 3 messages: Human, AI-with-toolcall, Tool).
"""
from __future__ import annotations

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage


class _ScriptedModel(GenericFakeChatModel):
    """A tool-calling-capable fake. ``bind_tools`` must return a runnable;
    the stock GenericFakeChatModel raises NotImplementedError there."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


class _RaisingModel(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self

    def _generate(self, *a, **k):  # pragma: no cover - exercised via ainvoke
        raise RuntimeError("model exploded")

    async def _agenerate(self, *a, **k):
        raise RuntimeError("model exploded")


def _toolcall_msg(args: dict) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{
            "name": "set_output",
            "args": args,
            "id": "call_1",
            "type": "tool_call",
        }],
    )


def test_subagent_context_middleware_adds_resilience_and_compaction():
    from vibecanvas_api.agent import AgentContext
    from vibecanvas_api.agents.tools.subagent.core import _subagent_context_middleware

    middleware = _subagent_context_middleware(AgentContext(agent_cfg={"model": "m"}))

    assert len(middleware) == 2
    assert type(middleware[0]).__name__ == "RuntimeResilienceMiddleware"
    mw = middleware[1]
    edit_names = [type(edit).__name__ for edit in mw.edits]
    assert edit_names == ["LifecyclePolicyEdit"]


@pytest.mark.asyncio
async def test_run_bounded_agent_terminates_on_set_output():
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent

    # FIRST message calls set_output; SECOND must never be consumed (return_direct).
    model = _ScriptedModel(messages=iter([
        _toolcall_msg({"answer": "42"}),
        AIMessage(content="this should never be reached"),
    ]))
    res = await run_bounded_agent(
        model=model,
        tools=[],
        system_prompt="be a worker",
        user_input="what is the answer?",
        output_fields={"answer": {"type": "string"}},
        max_iterations=10,
    )
    assert res.status == "done"
    assert res.output == {"answer": "42"}
    assert res.trace, "trace should be non-empty"
    # Proof of termination via return_direct: the SECOND scripted AIMessage
    # ("this should never be reached") was NEVER consumed — the loop stopped the
    # instant set_output ran. The trace is exactly System + Human + AI(tool_call)
    # + Tool(set_output result), with no further model turn.
    assert all("never be reached" not in m["text"] for m in res.trace)
    roles = [m["role"] for m in res.trace]
    assert roles == ["system", "human", "ai", "tool"]
    # The terminal tool call is present and is the LAST model action.
    assert res.trace[2]["tool_calls"] == [
        {"name": "set_output", "args": {"answer": "42"}}
    ]
    assert res.error is None


@pytest.mark.asyncio
async def test_run_bounded_agent_incomplete_when_no_set_output():
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent

    # The model only ever emits plain text — never calls the terminal tool.
    model = _ScriptedModel(messages=iter([
        AIMessage(content="I am just chatting and never finishing."),
    ]))
    res = await run_bounded_agent(
        model=model,
        tools=[],
        system_prompt="be a worker",
        user_input="do the thing",
        output_fields={"answer": {"type": "string"}},
        max_iterations=3,
    )
    assert res.status == "incomplete"
    assert res.output == {"answer": ""}
    assert res.error is None


@pytest.mark.asyncio
async def test_reused_context_does_not_leak_stale_output():
    from vibecanvas_api.agent import AgentContext
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent

    # The agent-as-tool reuses the MAIN agent's ctx across run_subagent calls.
    ctx = AgentContext()

    # First call DOES set output via the terminal tool → done.
    m1 = _ScriptedModel(messages=iter([
        _toolcall_msg({"answer": "first"}),
        AIMessage(content="never reached"),
    ]))
    r1 = await run_bounded_agent(
        model=m1,
        tools=[],
        system_prompt="be a worker",
        user_input="do it",
        output_fields={"answer": {"type": "string"}},
        max_iterations=10,
        context=ctx,
    )
    assert r1.status == "done"
    assert r1.output == {"answer": "first"}

    # Second call (SAME ctx) NEVER calls set_output → must NOT return the stale
    # "first"; status is incomplete with empty fields.
    m2 = _ScriptedModel(messages=iter([
        AIMessage(content="just chatting, never finishing"),
    ]))
    r2 = await run_bounded_agent(
        model=m2,
        tools=[],
        system_prompt="be a worker",
        user_input="do it again",
        output_fields={"answer": {"type": "string"}},
        max_iterations=3,
        context=ctx,
    )
    assert r2.status == "incomplete"
    assert r2.output == {"answer": ""}


@pytest.mark.asyncio
async def test_error_path_never_raises():
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent

    model = _RaisingModel(messages=iter([AIMessage(content="unused")]))
    res = await run_bounded_agent(
        model=model,
        tools=[],
        system_prompt="be a worker",
        user_input="do the thing",
        output_fields={"answer": {"type": "string"}},
        max_iterations=3,
    )
    assert res.status == "error"
    assert res.output == {"answer": ""}
    assert res.error


def _tool_then_again_msg() -> AIMessage:
    """An AIMessage that calls a (nonexistent harmless) tool so the ReAct loop
    keeps taking super-steps instead of finishing — used to overrun the limit."""
    return AIMessage(
        content="thinking...",
        tool_calls=[{
            "name": "noop",
            "args": {},
            "id": "call_loop",
            "type": "tool_call",
        }],
    )


@pytest.mark.asyncio
async def test_recursion_overrun_is_incomplete_not_error():
    from langchain_core.tools import tool

    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent

    @tool
    def noop() -> str:
        """A harmless tool the model keeps calling so it never finishes."""
        return "ok"

    def _forever():
        while True:
            yield _tool_then_again_msg()

    # The model NEVER calls set_output and keeps calling noop → it overruns the
    # recursion bound. A GraphRecursionError must surface as "incomplete"
    # (bounded budget reached), NOT a generic "error".
    model = _ScriptedModel(messages=_forever())
    res = await run_bounded_agent(
        model=model,
        tools=[noop],
        system_prompt="be a worker",
        user_input="loop forever",
        output_fields={"answer": {"type": "string"}},
        max_iterations=2,
    )
    assert res.status == "incomplete"
    assert res.output == {"answer": ""}
    assert res.error == "max_iterations reached"


@pytest.mark.asyncio
async def test_run_bounded_agent_cancelled_when_stop_event_set():
    import threading

    from vibecanvas_api.agent import AgentContext
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent

    # A stop_event that is ALREADY set → run-level cancellation.
    e = threading.Event()
    e.set()
    ctx = AgentContext(stop_event=e)

    # The model would RAISE if ever invoked — proving the short-circuit happens
    # BEFORE the model is built/invoked (RuntimeError would surface as error
    # "RuntimeError: model exploded", not "cancelled").
    model = _RaisingModel(messages=iter([AIMessage(content="must not run")]))

    res = await run_bounded_agent(
        model=model,
        tools=[],
        system_prompt="be a worker",
        user_input="do the thing",
        output_fields={"answer": {"type": "string"}},
        max_iterations=3,
        context=ctx,
    )
    assert res.status == "error"
    assert res.error == "cancelled"
    assert res.output == {"answer": ""}
    assert res.trace == []
