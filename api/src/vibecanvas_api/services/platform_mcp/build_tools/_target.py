"""Helpers for resolving the workflow targeted by Platform MCP build tools."""

from __future__ import annotations

from vibecanvas_api.agents.tools.decorator import ToolError


def target_workflow_id(ctx) -> str:
    """Return the real workflow id build tools should operate on."""
    wf_id = getattr(ctx, "current_workflow_id", None)
    if not wf_id:
        fallback = getattr(ctx, "wf_id", "") or ""
        if fallback and not fallback.startswith("__"):
            wf_id = fallback
    if not wf_id:
        raise ToolError(
            "no_workflow",
            "No workflow is associated with this chat. Use list_workflows and "
            "set_workflow, or create_workflow, before calling workflow tools.",
        )
    return wf_id
