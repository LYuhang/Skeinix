"""Safety middleware for pre-execution approval of high-risk tool calls.

The control-flow owner is the outer agent loop: it creates HITL requests,
streams approval UI, waits for the user, and then resumes graph execution.
This middleware is deliberately not a pause/resume controller. It only enforces
the loop's decision at the final tool execution boundary, so a high-risk tool
cannot run if the outer loop was bypassed or the user denied the request.

``approval_mode`` is intentionally scoped to this pre-tool gate. It must never
resolve or bypass post-tool review / elicitation such as a
``render_interactive(wait_for_submit)`` card; those interactions follow the
tool's own ``completion_mode`` independently.
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from vibecanvas_api.services.agent_runtime.approval import (
    PRE_TOOL_APPROVAL_TOOLS,
    PreToolApprovalPolicy,
    is_pre_tool_approval_candidate,
)

# Public compatibility alias for callers that only inspect the registry. Policy
# evaluation itself is host-owned through PreToolApprovalPolicy.
PRE_APPROVAL_TOOLS = PRE_TOOL_APPROVAL_TOOLS
_POLICY = PreToolApprovalPolicy()


def _json_safe(value: Any) -> dict:
    if isinstance(value, dict):
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:
            return {k: str(v) for k, v in value.items()}
    return {}


def _requires_approval(tool_name: str, args: dict[str, Any], approval_mode: str) -> bool:
    return _POLICY.evaluate(
        approval_mode=approval_mode,
        source="langchain",
        tool_name=tool_name,
        arguments=args,
    ).action == "wait"


def requires_user_approval(tool_name: str, args: dict[str, Any], approval_mode: str) -> bool:
    return _requires_approval(tool_name, args, approval_mode)


def denied_tool_message(tool_name: str, tool_call_id: str, reason: str = "user_denied") -> ToolMessage:
    content = "Tool call was not executed because user approval was not granted."
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=tool_name,
        artifact={
            "schema_version": 1,
            "status": "error",
            "error": {"code": reason, "message": content},
            "content": content,
            "content_abstract": f"{tool_name} → user approval denied",
            "ref": f"tool://{tool_name or 'tool'}/approval_denied",
            "payload": {"not_executed": True, "reason": reason},
            "meta": {
                "tool": tool_name,
                "hitl_type": "pre_tool_approval",
                "not_executed": True,
            },
        },
        response_metadata={"finish_reason": "approval_denied"},
    )


class UserApprovalMiddleware(AgentMiddleware):
    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        tool_call = request.tool_call or {}
        tool_name = str(tool_call.get("name") or "")
        args = _json_safe(tool_call.get("args") or {})
        if not is_pre_tool_approval_candidate(tool_name, args):
            return await handler(request)
        ctx = getattr(request.runtime, "context", None) if request.runtime else None
        tool_call_id = str(tool_call.get("id") or "")
        decisions = getattr(ctx, "tool_approval_decisions", {}) or {}
        decision = str(decisions.get(tool_call_id) or "")
        if decision == "approved":
            return await handler(request)
        return denied_tool_message(tool_name, tool_call_id, reason=decision or "approval_missing")
