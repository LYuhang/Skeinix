"""TokenRecordMiddleware — record ``meta.tokens`` at MESSAGE-CREATION time
before context-editing middleware runs.

The initial P2a implementation recorded ``meta.tokens`` inside the compaction
``LifecyclePolicyEdit``, which runs on the ``ContextEditingMiddleware`` per-call
DEEP-COPY of the messages. That copy is transient — it is never written back to
the LangGraph checkpointer, so the counts were re-computed every turn and never
persisted.

This middleware moves recording to the message-creation seam, on the REAL agent
state messages (NOT a deep copy). Empirically verified (langchain 1.2.18 +
LangGraph): in-place mutation of a message's metadata inside ``before_model`` /
``after_model``, and mutation of the ToolMessage returned from
``wrap_tool_call``, are all persisted by the checkpointer (the checkpoint stores
the actual state objects after each node runs). So recording once here makes the
count ride the checkpointer; the running context size is then a cheap running
SUM and the compaction stages only READ ``meta.tokens`` (stamping ``form`` /
``compressed`` as they degrade — never recomputing ``raw``).

Stamps:
  - ``after_model``     → the fresh AIMessage (raw = its content).
  - ``wrap_tool_call``  → the fresh ToolMessage result (raw + abstract via the
                          envelope, using ``build_message_tokens``).
  - ``before_model``    → the latest HumanMessage at turn start, IF not already
                          stamped (the human turn has no creation hook of its
                          own, so we stamp it the first time we see it).

Everything is fail-soft: a token-count error must never break a turn. This
middleware is placed BEFORE ``ContextEditingMiddleware`` in the agent stack so
the counts exist by the time compaction reads them.
"""
from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from vibecanvas_api.agents.token_accounting import (
    message_tokens, record_message_tokens,
)


class TokenRecordMiddleware(AgentMiddleware):
    """Record ``meta.tokens`` on messages as they are created, so the counts
    persist in the checkpointer. See the module docstring."""

    def __init__(self, *, model: str = "") -> None:
        super().__init__()
        # The active agent model — its tokenizer produces model-specific counts.
        self.model = model or ""

    # ------------------------------------------------------------------ #
    # before_model — stamp the latest unstamped HumanMessage
    # ------------------------------------------------------------------ #
    def before_model(self, state: Any, runtime: Any = None) -> None:  # noqa: ANN401
        try:
            messages = (state or {}).get("messages") or []
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    if message_tokens(msg) is None:
                        record_message_tokens(msg, model=self.model, form="raw")
                    break
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    # after_model — stamp the fresh AIMessage
    # ------------------------------------------------------------------ #
    def after_model(self, state: Any, runtime: Any = None) -> None:  # noqa: ANN401
        try:
            messages = (state or {}).get("messages") or []
            if not messages:
                return None
            last = messages[-1]
            if isinstance(last, AIMessage) and message_tokens(last) is None:
                record_message_tokens(last, model=self.model, form="raw")
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    # wrap_tool_call — stamp the fresh ToolMessage result
    # ------------------------------------------------------------------ #
    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:  # noqa: ANN401
        result = handler(request)
        self._stamp_tool_result(result)
        return result

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:  # noqa: ANN401
        # The agent runs via astream()/ainvoke() → langchain requires the ASYNC
        # variant (a sync-only wrap_tool_call raises NotImplementedError in an
        # async context, which the ToolNode then surfaces as a tool error and the
        # ReAct loop stops after the first tool call). Mirror the sync logic; the
        # async handler must be awaited.
        result = await handler(request)
        self._stamp_tool_result(result)
        return result

    def _stamp_tool_result(self, result: Any) -> None:
        try:
            if isinstance(result, ToolMessage) and message_tokens(result) is None:
                # build_message_tokens (inside record_message_tokens) parses the
                # envelope → fills `abstract` for a tool-output envelope.
                record_message_tokens(result, model=self.model, form="raw")
        except Exception:
            pass
