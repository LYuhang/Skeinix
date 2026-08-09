"""Shared helpers for Platform MCP workflow run tools."""
from __future__ import annotations

import asyncio

from vibecanvas_api.services.platform_mcp.build_tools._target import target_workflow_id
from vibecanvas_api.agents.tools._session_fs import _resolve_session


def _resolve_session_sync(ctx):
    """Sync wrapper around _resolve_session for the sync run path (runs in a
    worker thread with no event loop). Pull-only + fail-soft; never raises."""
    try:
        return asyncio.run(_resolve_session(ctx))
    except Exception:
        return None


def _save_if_dirty(ctx) -> str | None:
    """Save-before-run: commit a subversion when the workflow has unpersisted
    edits, then run against the just-saved graph — mirroring the client's
    ``saveBeforeRun`` (dirty → save → run; clean → run; save fails → abort).
    Shared by run_workflow + node_execute. Returns an error string when the save
    fails (the caller aborts the run), else ``None``."""
    if not getattr(ctx, "workflow_dirty", False):
        return None
    repo = getattr(ctx, "repo", None)
    if not repo:
        return "no repo available to save before run"
    try:
        wf_id = target_workflow_id(ctx)
        repo.commit(wf_id, ctx.workflow, note="Auto-save before run")
        repo.mark_saved(wf_id)
        ctx.workflow_dirty = False
        return None
    except Exception as e:  # save failed → run is NEVER started
        return f"save before run failed: {e}"
