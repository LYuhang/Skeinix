"""Shared session helpers for the agent's tools.

``_resolve_session`` / ``_require_session`` resolve the resident
:class:`SandboxSession` for the turn. Every host-touching file tool now
runs IN-SANDBOX via ``session.<op>`` — there is no host path mapping or VFS
fallback left here: ``_require_session`` raises when no sandbox is available,
``_resolve_session`` returns ``None`` for the best-effort callers.
"""
from __future__ import annotations

from vibecanvas_api.agents.tools.decorator import ToolError


async def _resolve_session(ctx):
    """Resolve the resident sandbox session for this turn, or ``None``.

    Pull-only: returns ``None`` when there is no session accessor, no
    tenant/wf identity, or the manager fails for any reason."""
    attached = getattr(ctx, "_attached_session", None)
    if attached is not None:
        return attached
    getter = getattr(ctx, "sandbox_session", None)
    if getter is None:
        return None
    try:
        return await getter()
    except Exception:
        return None


async def _require_session(ctx):
    """The resident sandbox or a hard ToolError — file tools run IN-SANDBOX
    and have no VFS fallback, so a turn with no sandbox is an explicit error."""
    session = await _resolve_session(ctx)
    if session is None:
        raise ToolError("no_workspace",
                        "no workspace is available — file operations require "
                        "an active workspace sandbox")
    return session
