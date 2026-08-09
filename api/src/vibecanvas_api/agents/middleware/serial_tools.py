# -*- coding: utf-8 -*-
"""SerialToolExecutionMiddleware (FU-2) — execute the agent's tool calls one at a
time, in order.

LangGraph's ToolNode runs the multiple tool_calls of a single AIMessage via
``asyncio.gather`` (parallel). Tools that share mutable state — e.g.
``run_workflow`` / ``node_execute`` both writing the session's fixed
``run_dir/__exec__`` — can then clobber each other. This middleware funnels every
tool call through one lock (per agent build = per turn), so a step's tool_calls
execute sequentially in their listed order. Tools are mostly IO and the model
usually emits one tool_call per step, so the latency cost is negligible; the
benefit is deterministic ordering and no shared-state races.
"""
from __future__ import annotations

import asyncio
from typing import Any

from langchain.agents.middleware import AgentMiddleware


class SerialToolExecutionMiddleware(AgentMiddleware):
    """Serialize tool execution within a turn via a per-instance lock."""

    def __init__(self) -> None:
        super().__init__()
        # One lock per middleware instance. The agent is (re)built per turn, so
        # this serializes a turn's tool_calls without coupling separate turns/chats.
        self._lock = asyncio.Lock()

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        async with self._lock:
            return await handler(request)
