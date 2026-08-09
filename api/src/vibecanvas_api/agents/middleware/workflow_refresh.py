"""WorkflowRefreshMiddleware — reload ctx.workflow from DB before build tools execute.

Without this, the agent operates on the workflow snapshot loaded at turn start
(chats.py:191). If the user had unsaved canvas edits that were committed just
before the turn (via the client-side save-before-send in run-agent-turn.ts),
this middleware ensures the agent picks up that fresh commit rather than the
older cached snapshot.

It also covers intra-turn races: if something committed to the workflow between
when the turn started and when the tool actually runs (e.g. a previous tool call
in the same turn), the next build tool always reads the true HEAD.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

_BUILD_TOOLS: frozenset[str] = frozenset({
    "check_workflow",
    "get_workflow",
    "update_canvas",
    "new_version",
    "run_workflow",
    "node_execute",
    "batch_execute",
})


class WorkflowRefreshMiddleware(AgentMiddleware):
    """Reload ctx.workflow from the committed DB HEAD before each build tool call.

    Fail-soft: any error during the refresh is swallowed and the tool proceeds
    with whatever ctx.workflow currently holds.
    """

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        tool_name = (request.tool_call or {}).get("name", "")
        if tool_name in _BUILD_TOOLS:
            ctx = getattr(request.runtime, "context", None) if request.runtime else None
            repo = getattr(ctx, "repo", None)
            wf_id = getattr(ctx, "current_workflow_id", None)
            if not wf_id and getattr(ctx, "surface", "") != "chat":
                wf_id = getattr(ctx, "wf_id", None)
            if ctx is not None and repo is not None and wf_id:
                try:
                    fresh = await asyncio.to_thread(repo.get_current_workflow, wf_id)
                    if fresh:
                        ctx.workflow = fresh
                except Exception:
                    pass
        return await handler(request)
