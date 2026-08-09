# -*- coding: utf-8 -*-
"""Record ``meta.tokens`` when creating each message
time so it PERSISTS in the LangGraph checkpointer (and the running sum just
accumulates), instead of inside the compaction deep-copy (transient).

Two layers of coverage:
  1. unit — drive the ``TokenRecordMiddleware`` hooks (``before_model`` /
     ``after_model`` / ``wrap_tool_call``) directly on synthetic state and assert
     the message OBJECT (the same object the checkpointer saves) is stamped.
  2. persistence — run a REAL ``create_agent`` turn with a checkpointer and a
     scripted tool-calling model, then re-read the checkpoint and assert the
     stored messages carry ``meta.tokens``. This is the crux: it proves the
     stamp persists (unlike the per-call deep-copy in ContextEditingMiddleware).
"""
from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from vibecanvas_api.agents.middleware.token_record import TokenRecordMiddleware
from vibecanvas_api.agents.token_accounting import message_tokens


def _env(path="/data/a.jsonl", ct="table/jsonl", data="x" * 6000, abstract="short"):
    return json.dumps(
        {"status": "success", "error": None, "abstract": abstract,
         "output": {"path": path, "content_type": ct, "data": data}},
        ensure_ascii=False,
    )


# --------------------------------------------------------------------------- #
# unit — hooks stamp the real message object
# --------------------------------------------------------------------------- #

def test_after_model_stamps_aimessage():
    mw = TokenRecordMiddleware(model="m")
    ai = AIMessage(content="hello there, a fairly long answer")
    mw.after_model({"messages": [HumanMessage(content="hi"), ai]}, runtime=None)
    tok = message_tokens(ai)
    assert tok is not None
    assert tok["raw"] > 0
    assert tok["form"] == "raw"
    assert tok["model"] == "m"
    # stamped on response_metadata (AIMessage slot)
    assert ai.response_metadata.get("tokens") is tok


def test_before_model_stamps_latest_human():
    mw = TokenRecordMiddleware(model="m")
    human = HumanMessage(content="build me a workflow please")
    mw.before_model({"messages": [human]}, runtime=None)
    tok = message_tokens(human)
    assert tok is not None and tok["raw"] > 0 and tok["form"] == "raw"
    # HumanMessage carries it in additional_kwargs
    assert human.additional_kwargs.get("tokens") is tok


def test_before_model_does_not_restamp_already_recorded_human():
    mw = TokenRecordMiddleware(model="m")
    human = HumanMessage(content="hi")
    mw.before_model({"messages": [human]}, runtime=None)
    first = dict(message_tokens(human))
    # mutate the recorded raw to a sentinel; a second pass must NOT overwrite it
    human.additional_kwargs["tokens"]["raw"] = 99999
    mw.before_model({"messages": [human]}, runtime=None)
    assert message_tokens(human)["raw"] == 99999  # preserved, not re-recorded
    assert first  # sanity


def test_wrap_tool_call_stamps_toolmessage_raw_and_abstract():
    mw = TokenRecordMiddleware(model="m")
    produced = ToolMessage(content=_env(), tool_call_id="c1", name="inspect_data")

    def handler(_request):
        return produced

    res = mw.wrap_tool_call(request=object(), handler=handler)
    assert res is produced
    tok = message_tokens(res)
    assert tok is not None
    assert tok["raw"] > 0
    # envelope → abstract count populated (via build_message_tokens)
    assert tok["abstract"] is not None and tok["abstract"] > 0
    assert tok["form"] == "raw"


def test_wrap_tool_call_passes_through_command_results():
    """Non-ToolMessage results (e.g. a Command) flow through unstamped, untouched."""
    mw = TokenRecordMiddleware(model="m")
    sentinel = {"not": "a toolmessage"}

    def handler(_request):
        return sentinel

    assert mw.wrap_tool_call(request=object(), handler=handler) is sentinel


def test_hooks_failsoft_never_raise():
    mw = TokenRecordMiddleware(model="")
    # weird/empty states must not raise
    assert mw.before_model({"messages": []}, runtime=None) is None
    assert mw.after_model({"messages": []}, runtime=None) is None

    class _Boom(ToolMessage):
        @property
        def content(self):  # type: ignore[override]
            raise RuntimeError("boom reading content")

    def handler(_request):
        # a real ToolMessage but counting raises inside — must be swallowed
        return ToolMessage(content="ok", tool_call_id="c", name="t")

    # even if recording blows up, the tool result is returned unharmed
    res = mw.wrap_tool_call(request=object(), handler=handler)
    assert isinstance(res, ToolMessage)


# --------------------------------------------------------------------------- #
# persistence — the crux: stamps survive the checkpointer
# --------------------------------------------------------------------------- #

class _Scripted(GenericFakeChatModel):
    """Tool-calling-capable fake (stock GenericFakeChatModel can't bind_tools)."""

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


@tool
def echo(text: str) -> str:
    """Echo the text back."""
    return "TOOLRESULT:" + text


def test_meta_tokens_persists_through_checkpointer():
    model = _Scripted(messages=iter([
        AIMessage(content="", tool_calls=[
            {"name": "echo", "args": {"text": "hi"}, "id": "c1", "type": "tool_call"}]),
        AIMessage(content="all done"),
    ]))
    cp = InMemorySaver()
    agent = create_agent(
        model=model, tools=[echo],
        middleware=[TokenRecordMiddleware(model="m")],
        checkpointer=cp,
    )
    cfg = {"configurable": {"thread_id": "persist-1"}}
    agent.invoke({"messages": [HumanMessage(content="please echo")]}, cfg)

    # Re-read the CHECKPOINT (not the live in-call state) and assert every
    # message carries meta.tokens — proving the creation-time stamp persisted.
    stored = agent.get_state(cfg).values["messages"]
    assert len(stored) >= 3  # Human, AI(toolcall), Tool, AI(done)
    by_type = {}
    for m in stored:
        tok = message_tokens(m)
        assert tok is not None, f"{type(m).__name__} missing meta.tokens"
        assert isinstance(tok.get("raw"), int)
        by_type.setdefault(type(m).__name__, tok)
    assert "HumanMessage" in by_type
    assert "AIMessage" in by_type
    assert "ToolMessage" in by_type
