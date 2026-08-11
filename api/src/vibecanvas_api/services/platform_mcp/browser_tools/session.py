"""Platform MCP browser session tools."""
from __future__ import annotations

import uuid
from typing import Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools.decorator import ToolError, tool_output
from vibecanvas_api.agents.tools.render import register_render, Rendered
from vibecanvas_api.services.platform_mcp.browser_tools._common import (
    _browser_health_lines,
    _browser_tool_error,
    _get_browser,
    _load_browser_binding,
    _run,
)


# ── browser_session_status ───────────────────────────────────────────────────

@register_render("browser_session_status")
def _render_session_status(raw: dict, ctx) -> Rendered:
    status = str(raw.get("status") or "inactive")
    connected = bool(raw.get("connected"))
    tabs = raw.get("tabs") if isinstance(raw.get("tabs"), list) else []
    lines = [
        f"Browser control status: {status}",
        f"Extension connection: {'connected' if connected else 'unavailable'}",
    ]
    if tabs:
        lines.append("Controlled tabs:")
        for tab in tabs:
            if not isinstance(tab, dict):
                continue
            tab_id = tab.get("tab")
            title = str(tab.get("title") or "")
            url = str(tab.get("url") or "")
            active = " [active]" if tab.get("active") else ""
            lines.append(f"[{tab_id}] {title!r}  {url}{active}")
    else:
        lines.append("Controlled tabs: none")
    lines.extend(_browser_health_lines(raw.get("health")))
    return Rendered(
        content="\n".join(lines),
        content_type="text/plain",
        abstract=f"browser_session_status → {status}, {len(tabs)} controlled tab(s)",
    )


@tool_output(content_type="text/plain", tool="browser_session_status")
async def _session_status(runtime: ToolRuntime) -> dict:
    ctx = runtime.context
    binding = await _load_browser_binding(ctx)
    status = str((binding or {}).get("status") or "inactive")
    connected = _get_browser(ctx) is not None
    result: dict = {"status": status, "connected": connected, "tabs": []}
    if connected:
        try:
            # Even an inactive durable session can have a debugger attachment
            # left behind by an MV3 worker restart. list_open_tabs is the safe,
            # pre-session observation path and carries the same health summary;
            # do not expose its unrelated tab list through this status tool.
            command = "list_tabs" if status == "attached" else "list_open_tabs"
            observation = await _run(command, ctx)
            data = observation.get("data") if isinstance(observation, dict) else {}
            if (
                status == "attached"
                and isinstance(data, dict)
                and isinstance(data.get("tabs"), list)
            ):
                result["tabs"] = data["tabs"]
            if isinstance(data, dict) and isinstance(data.get("health"), dict):
                result["health"] = data["health"]
        except ToolError:
            # Match the filesystem tools: errors are raised and normalized by
            # @tool_output, never returned inside a success payload.
            raise
    return result


@tool(response_format="content_and_artifact")
async def browser_session_status(
    require_user_auth: bool = True,
    approval_reason: str = "",
    *,
    runtime: ToolRuntime,
) -> str:
    """Inspect durable browser-control state and the currently controlled tabs.

    Call this before recovery after a disconnect, timeout, stale tab id, or
    uncertain command result. It is read-only, so the Agent may set
    ``require_user_auth=false`` when no user approval is needed. Returned tab ids
    can be passed explicitly to page read/write tools.

    Args:
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.
    """
    return await _session_status(runtime)


# ── browser_check_login ───────────────────────────────────────────────────────

@register_render("browser_check_login")
def _render_check_login(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    logged_in = data.get("logged_in", False)
    content = "Logged in." if logged_in else "Not logged in."
    return Rendered(content=content, content_type="text/plain",
                    abstract=f"browser_check_login → logged_in={logged_in}")


@tool_output(content_type="text/plain", tool="browser_check_login")
async def _check_login(tab: int, runtime: ToolRuntime) -> dict:
    return await _run("check_login", runtime.context, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_check_login(tab: int = 0, *, runtime: ToolRuntime) -> str:
    """Check whether the user is authenticated on the current page.

    e.g. verify login before reading account-specific content such as an order
    history, a personal dashboard, or a members-only section.

    Args:
        tab: the tab to check (0 = controlled tab).

    Returns:
        content = "Logged in." or "Not logged in." — never exposes credentials.
    """
    return await _check_login(tab, runtime)


# ── browser_start_session ────────────────────────────────────────────────────

@register_render("browser_start_session")
def _render_start_session(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    tab = raw.get("tab") or data.get("tab")
    started = data.get("started", True)
    status = "started" if started else "already active"
    lines = [
        f"Browser session {status}.",
        f"Controlling tab: {tab}" if tab is not None else "Controlling tab: unavailable",
    ]
    content = "\n".join(lines)
    return Rendered(content=content, content_type="text/plain",
                    abstract=f"browser_start_session → tab {tab}")


@tool_output(content_type="text/plain", tool="browser_start_session")
async def _start_session(
    target: Literal["current", "new", "existing"],
    tab: int,
    runtime: ToolRuntime,
) -> dict:
    if _get_browser(runtime.context) is None:
        raise _browser_tool_error(
            "no_browser",
            "Browser control transport is unavailable; no session was started.",
            not_executed=True,
        )
    effective_target = target or "current"
    if effective_target == "existing" and int(tab or 0) <= 0:
        raise ToolError(
            "browser_tab_required",
            "target='existing' requires a tab id returned by browser_tab(action='list_open').",
        )
    if effective_target != "existing" and int(tab or 0) > 0:
        raise ToolError(
            "browser_tab_not_applicable",
            "The tab parameter is only valid when target='existing'.",
        )
    ctx = runtime.context
    requested_session_id = f"brs_{uuid.uuid4().hex}"
    binding = await _reserve_browser_session(ctx, requested_session_id)
    browser_session_id = str((binding or {}).get("browser_session_id") or requested_session_id)
    session_generation = int((binding or {}).get("browser_session_generation") or 0)
    extension_attached = False
    try:
        obs = await _run(
            "start_session",
            ctx,
            target=effective_target,
            tab=tab or None,
            browser_session_id=browser_session_id,
            session_generation=session_generation,
        )
        extension_attached = True
        confirm = await _confirm_browser_session_attached(
            ctx,
            browser_session_id=browser_session_id,
            browser_session_generation=session_generation,
        )
        if confirm:
            obs_data = obs.setdefault("data", {})
            if isinstance(obs_data, dict):
                obs_data.setdefault("browser_session_id", browser_session_id)
                obs_data.setdefault("session_generation", session_generation)
        return obs
    except Exception:
        # If Chrome attached but the durable ``attached`` transition failed,
        # compensate on the extension before rolling the reservation back.
        # Cleanup is deliberately best-effort so the original ToolError remains
        # the standardized result seen by the Agent; the extension lifecycle
        # event is the second durable-release path if this command races detach.
        if extension_attached:
            try:
                await _run(
                    "end_session",
                    ctx,
                    reason="start_session_persist_failed",
                    browser_session_id=browser_session_id,
                    session_generation=session_generation,
                )
            except Exception:
                pass
        await _persist_browser_released(
            ctx,
            "start_session_failed",
            browser_session_id=browser_session_id,
            browser_session_generation=session_generation or None,
            best_effort=True,
        )
        raise


@tool(response_format="content_and_artifact")
async def browser_start_session(
    target: Literal["current", "new", "existing"] = "current",
    tab: int = 0,
    require_user_auth: bool = True,
    approval_reason: str = "",
    *,
    runtime: ToolRuntime,
) -> str:
    """Start a browser control session.

    `target` controls which tab the session attaches to:
    - "current" (default) — attach to the tab the user is currently viewing.
      e.g. the user has a form open and says "fill this in for me."
    - "new" — open a fresh blank tab.
      e.g. "look up the cheapest option" — run it in a new tab without
      disturbing what the user already has open.
    - "existing" — attach to a tab id returned by browser_tab(action="list_open").

    When successful, returns the stable tab id for browser operations. Errors are
    raised as ToolError and normalized by the shared tool decorator.

    Args:
        target: current, new, or existing.
        tab: stable tab id for target="existing".
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = session status and controlled tab id.
    """
    return await _start_session(target, tab, runtime)


@tool_output(content_type="text/plain", tool="browser_end_session")
async def _end_session(reason: str, runtime: ToolRuntime) -> dict:
    ctx = runtime.context
    binding = await _load_persisted_browser_binding(ctx)
    status = str((binding or {}).get("status") or "inactive")
    if status == "inactive":
        raise _browser_tool_error(
            "browser_session_released",
            "Browser control is already released; no end-session command was executed.",
            not_executed=True,
        )
    if _get_browser(ctx) is None:
        raise _browser_tool_error(
            "browser_connection_lost",
            "The extension is disconnected, so release could not be confirmed.",
            not_executed=True,
        )
    obs = await _run("end_session", ctx, reason=reason or "agent_requested")
    await _persist_browser_released(
        ctx,
        reason or "agent_requested",
        browser_session_id=str((binding or {}).get("browser_session_id") or "") or None,
        browser_session_generation=(
            int((binding or {}).get("browser_session_generation") or 0) or None
        ),
    )
    return obs


@tool(response_format="content_and_artifact")
async def browser_end_session(
    reason: str = "agent_requested",
    require_user_auth: bool = True,
    approval_reason: str = "",
    *,
    runtime: ToolRuntime,
) -> str:
    """Release browser control for the current browser session.

    Use this when the task is complete or the user asks you to stop controlling
    the browser. This releases all controlled tabs; it does not cancel or delete
    the chat history.

    Args:
        reason: short reason for audit/UI.
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = release confirmation.
    """
    return await _end_session(reason, runtime)


SESSION_TOOLS = [
    browser_session_status,
    browser_check_login,
    browser_start_session,
    browser_end_session,
]


async def _reserve_browser_session(ctx, browser_session_id: str) -> dict:
    tenant_id = getattr(ctx, "tenant_id", None)
    chat_id = getattr(ctx, "chat_id", None)
    if not tenant_id or not chat_id:
        raise ToolError(
            "browser_context_missing",
            "Browser session reservation requires tenant_id and chat_id from a persisted agent turn.",
        )
    try:
        from vibecanvas_api.storage.chat_repo import ChatRepo
        from vibecanvas_api.storage.db import session_scope
        from vibecanvas_api.config import config

        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = ChatRepo(session, getattr(ctx, "username", "") or "")
            result = await repo.reserve_browser_session(
                chat_id=str(chat_id),
                browser_session_id=browser_session_id,
                lost_grace_seconds=config.browser_lost_grace_seconds,
            )
            if not result.get("ok"):
                code = str(result.get("error_code") or "browser_session_conflict")
                raise ToolError(
                    code,
                    f"[{code}] This chat cannot take browser control because "
                    "the user's browser-control session is already bound to "
                    "another chat or disconnected.",
                    info={
                        "conflicting_chat_id": result.get("conflicting_chat_id"),
                        "binding": result.get("binding"),
                    },
                )
            binding = result.get("binding")
            if not isinstance(binding, dict):
                raise ToolError(
                    "browser_session_persist_failed",
                    "The durable browser session reservation returned no binding state.",
                )
            return binding
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            "browser_session_persist_failed",
            "Browser control could not start because the durable session reservation failed.",
        ) from exc


async def _confirm_browser_session_attached(
    ctx,
    *,
    browser_session_id: str,
    browser_session_generation: int,
) -> dict:
    tenant_id = getattr(ctx, "tenant_id", None)
    chat_id = getattr(ctx, "chat_id", None)
    if not tenant_id or not chat_id or not browser_session_id:
        raise ToolError(
            "browser_context_missing",
            "Browser session confirmation requires tenant_id, chat_id, and browser_session_id.",
        )
    from vibecanvas_api.storage.chat_repo import ChatRepo
    from vibecanvas_api.storage.db import session_scope

    try:
        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = ChatRepo(session, getattr(ctx, "username", "") or "")
            result = await repo.confirm_browser_session_attached(
                chat_id=str(chat_id),
                browser_session_id=browser_session_id,
                browser_session_generation=browser_session_generation,
            )
            if not result.get("ok"):
                code = str(result.get("error_code") or "browser_session_state_mismatch")
                raise ToolError(
                    code,
                    "Browser session attach succeeded, but backend state confirmation failed.",
                )
            binding = result.get("binding")
            if not isinstance(binding, dict):
                raise ToolError(
                    "browser_session_persist_failed",
                    "The durable browser session confirmation returned no binding state.",
                )
            return binding
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            "browser_session_persist_failed",
            "Browser control attached, but saving its durable state failed. End control and retry.",
        ) from exc


async def _load_persisted_browser_binding(ctx) -> dict | None:
    tenant_id = getattr(ctx, "tenant_id", None)
    chat_id = getattr(ctx, "chat_id", None)
    if not tenant_id or not chat_id:
        raise ToolError(
            "browser_context_missing",
            "Browser session state requires tenant_id and chat_id from a persisted agent turn.",
        )
    from vibecanvas_api.storage.chat_repo import ChatRepo
    from vibecanvas_api.storage.db import session_scope

    try:
        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = ChatRepo(session, getattr(ctx, "username", "") or "")
            binding = await repo.get_browser_binding(str(chat_id))
    except ToolError:
        raise
    except Exception as exc:
        raise ToolError(
            "browser_session_state_unavailable",
            "Durable browser-control state could not be loaded.",
        ) from exc
    return binding if isinstance(binding, dict) else None


async def _persist_browser_released(
    ctx,
    reason: str,
    *,
    browser_session_id: str | None,
    browser_session_generation: int | None,
    best_effort: bool = False,
) -> None:
    tenant_id = getattr(ctx, "tenant_id", None)
    chat_id = getattr(ctx, "chat_id", None)
    if not tenant_id or not chat_id:
        if best_effort:
            return
        raise ToolError(
            "browser_context_missing",
            "Browser release persistence requires tenant_id and chat_id.",
        )
    try:
        from vibecanvas_api.storage.chat_repo import ChatRepo
        from vibecanvas_api.storage.db import session_scope

        async with session_scope(tenant_id=str(tenant_id)) as session:
            repo = ChatRepo(session, getattr(ctx, "username", "") or "")
            result = await repo.release_browser_session(
                str(chat_id),
                browser_session_id=browser_session_id,
                browser_session_generation=browser_session_generation,
                reason=reason,
            )
            if not result.get("ok"):
                code = str(result.get("error_code") or "browser_release_persist_failed")
                raise ToolError(
                    code,
                    "Browser control ended, but the durable release state could not be confirmed.",
                )
    except ToolError:
        if not best_effort:
            raise
    except Exception as exc:
        if not best_effort:
            raise ToolError(
                "browser_release_persist_failed",
                "Browser control ended, but saving the durable release state failed. "
                "Call browser_session_status before the next browser operation.",
            ) from exc
