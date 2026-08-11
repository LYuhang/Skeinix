"""Shared helpers for the Platform MCP browser tool layer.

Each browser tool calls `_run(cmd, ctx, **kwargs)` which dispatches one command to
the live browser transport, raises ToolError on failure, and returns the raw
observation dict on success. Images and screenshots arrive already materialised as
VFS paths (never raw bytes) — the media host substitutes them before the observation
reaches any tool.
"""
from __future__ import annotations

import asyncio

from vibecanvas_api.agents.tools.decorator import ToolError
from vibecanvas_api.browser.agent_channel import build_agent_browser
from vibecanvas_api.browser.commands import Cmd
from vibecanvas_api.browser.host import CommandResultUnknown, TransportClosed


PRE_SESSION_CMDS = {"start_session", "list_open_tabs"}
SESSION_OPTIONAL_CMDS = PRE_SESSION_CMDS | {"end_session", "list_tabs"}

_RECOVERY_HINTS = {
    "browser_context_missing": (
        "Run the browser tool from a persisted chat turn so durable browser state can be recorded."
    ),
    "browser_session_state_unavailable": (
        "Do not execute a browser mutation. Retry after the durable chat state is available."
    ),
    "no_browser": "Open or reconnect the browser side panel, then call browser_session_status.",
    "browser_connection_lost": (
        "Wait for reconnection. Then call browser_session_status and read the target tab before continuing."
    ),
    "browser_session_released": (
        "Do not retry the operation. Ask the user before calling browser_start_session again."
    ),
    "browser_session_mismatch": (
        "This command belongs to an old session. Call browser_session_status and refresh the tab list."
    ),
    "browser_command_result_unknown": (
        "Do not directly retry a write. First read the target page to verify whether it already took effect."
    ),
    "browser_debugger_conflict": (
        "Call browser_tab(action='list_open') and browser_session_status, then choose a fresh tab id. "
        "If the selected live tab still reports an external debugger conflict, do not detach it "
        "automatically; ask the user to close DevTools/other automation or choose another tab."
    ),
    "tab_not_found": "Call browser_tab(action='list_open') or browser_tab(action='list') and use a returned tab id.",
    "tab_not_controlled": "Call browser_tab(action='list') to get controlled tab ids, or start control for an open tab.",
    "tab_out_of_scope": "Use a tab id returned for the extension's current browser window.",
    "element_handle_stale": "Read or query the page again and retry with a newly returned element handle.",
    "scope_not_found": "Read the page again without that scope, then choose a fresh handle or selector.",
    "invalid_selector": "Use a valid CSS selector or read/query the page to obtain a handle.",
    "new_tab_timeout": "Inspect the current tab list before deciding whether the action must be repeated.",
    "wait_timeout": "Read the page state, adjust the condition or timeout, and retry only if still necessary.",
    "browser_command_not_executed": "Refresh the relevant browser state and correct the command inputs before retrying.",
    "invalid_save_path": "Use an exact file path under /data/, or omit save_path to use the default browser-media path.",
}


def _normalize_browser_error_code(code: str, detail: str) -> str:
    """Turn low-level extension details into stable, actionable error codes."""
    raw = str(code or "browser_error")
    text = str(detail or "").lower()
    if raw in {"browser_error", "browser_command_not_executed"}:
        if "no element matched" in text or "element not found" in text:
            return "element_handle_stale"
        if "invalid css selector" in text:
            return "invalid_selector"
        if "no new tab opened" in text:
            return "new_tab_timeout"
        if "wait_for timeout" in text:
            return "wait_timeout"
        if "tab" in text and ("not found" in text or "no such target" in text):
            return "tab_not_found"
    if raw in {
        "browser_error",
        "browser_command_not_executed",
        "browser_start_session_failed",
        "browser_command_failed",
    } and ("another debugger" in text or "already attached" in text):
        return "browser_debugger_conflict"
    return raw


def _browser_health_lines(health: dict | None) -> list[str]:
    """Render only the stable, privacy-preserving browser health contract."""
    if not isinstance(health, dict) or not health:
        return []
    state = str(health.get("state") or "unknown")
    stale = int(health.get("stale_attachment_count") or 0)
    conflicts = int(health.get("conflict_count") or 0)
    missing = int(health.get("missing_attachment_count") or 0)
    action = str(health.get("recommended_action") or "inspect_browser_state")
    return [
        f"Browser health: {state} "
        f"(stale_extension={stale}, external_conflicts={conflicts}, missing={missing})",
        f"Recommended action: {action}",
    ]


def _browser_tool_error(
    code: str,
    detail: str,
    *,
    effect_status: str | None = None,
    not_executed: bool = False,
    health: dict | None = None,
) -> ToolError:
    stable_code = _normalize_browser_error_code(code, detail)
    recovery = _RECOVERY_HINTS.get(
        stable_code,
        "Call browser_session_status and inspect the relevant tab before retrying.",
    )
    suffixes = []
    if effect_status:
        suffixes.append(f"effect_status={effect_status}")
    if not_executed:
        suffixes.append("not_executed=true")
    suffix = f" ({', '.join(suffixes)})" if suffixes else ""
    health_lines = _browser_health_lines(health)
    health_text = "\n".join(health_lines)
    extra = f"\n{health_text}" if health_text else ""
    message = f"[{stable_code}] {detail}{suffix}{extra}\nRecovery: {recovery}"
    return ToolError(
        stable_code,
        message,
        info={
            "recovery_hint": recovery,
            "effect_status": effect_status,
            "not_executed": bool(not_executed),
            "health": health if isinstance(health, dict) else None,
        },
    )


def _get_browser(ctx):
    """Return the live AgentBrowser bound to this turn's transport, or None."""
    return build_agent_browser(ctx)


async def _load_browser_binding(ctx) -> dict | None:
    tenant_id = getattr(ctx, "tenant_id", None)
    chat_id = getattr(ctx, "chat_id", None)
    if not tenant_id or not chat_id:
        raise _browser_tool_error(
            "browser_context_missing",
            "Browser tools require tenant_id and chat_id from a persisted agent turn.",
            not_executed=True,
        )
    from vibecanvas_api.storage.chat_repo import ChatRepo
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.config import config

    try:
        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = ChatRepo(session, getattr(ctx, "username", "") or "")
            binding = await repo.expire_stale_browser_lost_session(
                str(chat_id),
                grace_seconds=config.browser_lost_grace_seconds,
            )
    except ToolError:
        raise
    except Exception as exc:
        raise _browser_tool_error(
            "browser_session_state_unavailable",
            "Durable browser-control state could not be loaded.",
            not_executed=True,
        ) from exc
    return binding if isinstance(binding, dict) else None


async def _apply_session_fencing(cmd: str, ctx, call: dict) -> None:
    if cmd == "start_session":
        call.setdefault("turn_id", getattr(ctx, "turn_id", "") or None)
        return
    binding = await _load_browser_binding(ctx)
    status = str((binding or {}).get("status") or "inactive")
    if status == "lost":
        raise _browser_tool_error(
            "browser_connection_lost",
            "Browser connection is currently lost.",
            not_executed=True,
        )
    if cmd == "list_open_tabs" and status == "inactive":
        call.setdefault("turn_id", getattr(ctx, "turn_id", "") or None)
        return
    if status != "attached" and cmd not in SESSION_OPTIONAL_CMDS:
        raise _browser_tool_error(
            "browser_session_released",
            "Browser control is not active; the operation was not executed.",
            not_executed=True,
        )
    if binding:
        call.setdefault("browser_session_id", binding.get("browser_session_id") or None)
        call.setdefault("session_generation", binding.get("browser_session_generation") or 0)
    call.setdefault("turn_id", getattr(ctx, "turn_id", "") or None)


async def _run(cmd: str, ctx, **call) -> dict:
    """Send one browser command and return the observation dict on success.

    Raises ToolError when the browser is unavailable, when the underlying command
    raises, or when the observation reports ok=false.
    """
    ab = _get_browser(ctx)
    if ab is None:
        raise _browser_tool_error(
            "no_browser",
            "Browser control transport is not available in this context.",
            not_executed=True,
        )
    await _apply_session_fencing(cmd, ctx, call)
    try:
        if hasattr(ab, "_send"):
            obs = await ab._send(Cmd(cmd), **call)
        else:
            obs = await getattr(ab, cmd)(**call)
    except (asyncio.TimeoutError, CommandResultUnknown) as exc:
        raise _browser_tool_error(
            "browser_command_result_unknown",
            "The browser command was sent, but the result was not confirmed.",
            effect_status="unknown",
        ) from exc
    except TransportClosed as exc:
        raise _browser_tool_error(
            "browser_connection_lost",
            "Browser connection is not available; the command was not delivered to a live extension.",
            not_executed=True,
        ) from exc
    except ToolError:
        raise
    except Exception as exc:
        raise _browser_tool_error("browser_error", str(exc)) from exc
    if not isinstance(obs, dict):
        raise _browser_tool_error(
            "browser_error",
            f"browser.{cmd}: unexpected response type",
        )
    if not obs.get("ok", False):
        # The typed host observation intentionally keeps protocol payload fields
        # inside ``data``.  Fakes sometimes return the flattened wire payload, so
        # consume both shapes without losing outcome/fencing metadata.
        data = obs.get("data") if isinstance(obs.get("data"), dict) else {}
        detail = (
            obs.get("error")
            or data.get("error")
            or "command returned ok=false with no detail"
        )
        error_code = (
            obs.get("error_code")
            or data.get("error_code")
            or obs.get("code")
            or data.get("code")
            or (
                "browser_command_result_unknown"
                if (obs.get("effect_status") or data.get("effect_status")) == "unknown"
                else None
            )
            or "browser_error"
        )
        effect_status = obs.get("effect_status") or data.get("effect_status")
        not_executed = obs.get("not_executed") or data.get("not_executed")
        error_info = (
            obs.get("error_info")
            if isinstance(obs.get("error_info"), dict)
            else data.get("error_info")
            if isinstance(data.get("error_info"), dict)
            else {}
        )
        health = (
            obs.get("health")
            if isinstance(obs.get("health"), dict)
            else data.get("health")
            if isinstance(data.get("health"), dict)
            else error_info.get("health")
            if isinstance(error_info.get("health"), dict)
            else None
        )
        raise _browser_tool_error(
            str(error_code),
            str(detail),
            effect_status=str(effect_status) if effect_status else None,
            not_executed=bool(not_executed),
            health=health,
        )
    return obs
