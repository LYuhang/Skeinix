"""Platform MCP browser act tools — mutating interactions with the page.

Each tool performs a real action that changes page state (a click really clicks,
a submit really submits). Shared optional params for every act tool:
- `expect` (str): a CSS selector that should appear after the action. Set this
  whenever the action may change the page — without it the tool returns as soon as
  the action fires, before the page has reacted. With `expect`, the tool waits
  until that selector appears.
- `purpose` (str): a short label for why this action is taken (used for logging).
"""
from __future__ import annotations

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools.decorator import tool_output
from vibecanvas_api.agents.tools.render import register_render, Rendered
from vibecanvas_api.services.platform_mcp.browser_tools._common import _run


def _expect_note(raw: dict) -> str:
    data = raw.get("data") or {}
    expect_met = data.get("expect_met")
    if expect_met is True:
        return " Expected element appeared."
    if expect_met is False:
        return " Expected element did not appear."
    return ""


# ── browser_click ─────────────────────────────────────────────────────────────

@register_render("browser_click")
def _render_click(raw: dict, ctx) -> Rendered:
    content = "Clicked." + _expect_note(raw)
    return Rendered(content=content, content_type="text/plain",
                    abstract="browser_click")


@tool_output(content_type="text/plain", tool="browser_click")
async def _click(handle: str, selector: str, purpose: str, expect: str,
                 tab: int, runtime: ToolRuntime) -> dict:
    return await _run("click", runtime.context,
                      handle=handle or None, selector=selector or None,
                      purpose=purpose or None, expect=expect or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_click(handle: str = "", selector: str = "", purpose: str = "",
                        expect: str = "", tab: int = 0,
                        require_user_auth: bool = True, approval_reason: str = "", *,
                        runtime: ToolRuntime) -> str:
    """Click an element on the page.

    Prefer `handle` over raw `selector` for reliability. If the click triggers a
    navigation or page update, set `expect` (a CSS selector that should appear after
    the click) so the tool waits for the page to settle before returning. Element
    handles become stale when the page changes — supply a fresh one when needed.
    e.g. click a submit button and wait for a confirmation banner: expect=".alert-success";
    click an accordion header to expand its section: expect=".panel-body".

    Args:
        handle: element handle targeting the element to click.
        selector: CSS selector as an alternative to handle.
        purpose: short label for why this click is performed (for logging).
        expect: CSS selector to wait for after the click.
        tab: the tab to act on (0 = controlled tab).
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = "Clicked." and whether the expected element appeared.
    """
    return await _click(handle, selector, purpose, expect, tab, runtime)


# ── browser_type ──────────────────────────────────────────────────────────────

@register_render("browser_type")
def _render_type(raw: dict, ctx) -> Rendered:
    content = "Typed." + _expect_note(raw)
    return Rendered(content=content, content_type="text/plain",
                    abstract="browser_type")


@tool_output(content_type="text/plain", tool="browser_type")
async def _type(handle: str, text: str, replace: bool, purpose: str, expect: str,
                tab: int, runtime: ToolRuntime) -> dict:
    return await _run("type", runtime.context,
                      handle=handle, text=text, replace=replace or None,
                      purpose=purpose or None, expect=expect or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_type(handle: str, text: str, replace: bool = False, purpose: str = "",
                       expect: str = "", tab: int = 0,
                       require_user_auth: bool = True, approval_reason: str = "", *,
                       runtime: ToolRuntime) -> str:
    """Type text into a field identified by `handle`.

    Focuses the field and types so that the page's input handlers fire. Use
    `replace=True` to clear the existing value first; the default appends. If typing
    triggers live filtering or a navigation, set `expect` so the tool waits for the
    page to settle.
    e.g. type a search query and wait for autocomplete suggestions: expect=".suggestion-list";
    overwrite a pre-filled address field with replace=True to replace the old value.

    Args:
        handle: element handle for the field to type into.
        text: the text to type.
        replace: True to clear the field before typing (default False, appends).
        purpose: short label for why this action is performed (for logging).
        expect: CSS selector to wait for after typing.
        tab: the tab to act on (0 = controlled tab).
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = "Typed." and whether the expected element appeared.
    """
    return await _type(handle, text, replace, purpose, expect, tab, runtime)


# ── browser_select_option ─────────────────────────────────────────────────────

@register_render("browser_select_option")
def _render_select_option(raw: dict, ctx) -> Rendered:
    content = "Option selected." + _expect_note(raw)
    return Rendered(content=content, content_type="text/plain",
                    abstract="browser_select_option")


@tool_output(content_type="text/plain", tool="browser_select_option")
async def _select_option(handle: str, option: str, purpose: str, expect: str,
                         tab: int, runtime: ToolRuntime) -> dict:
    return await _run("select", runtime.context,
                      handle=handle, option=option,
                      purpose=purpose or None, expect=expect or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_select_option(handle: str, option: str, purpose: str = "",
                                expect: str = "", tab: int = 0, *,
                                require_user_auth: bool = True,
                                approval_reason: str = "",
                                runtime: ToolRuntime) -> str:
    """Select an option in a dropdown element.

    e.g. choose a country from a location dropdown; pick a size or quantity
    from a product option selector before adding an item to a cart.

    Args:
        handle: element handle for the dropdown.
        option: the option value or visible label to select.
        purpose: short label for why this action is performed (for logging).
        expect: CSS selector to wait for after the selection.
        tab: the tab to act on (0 = controlled tab).
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = "Option selected." and whether the expected element appeared.
    """
    return await _select_option(handle, option, purpose, expect, tab, runtime)


# ── browser_press_key ─────────────────────────────────────────────────────────

@register_render("browser_press_key")
def _render_press_key(raw: dict, ctx) -> Rendered:
    content = "Key pressed." + _expect_note(raw)
    return Rendered(content=content, content_type="text/plain",
                    abstract="browser_press_key")


@tool_output(content_type="text/plain", tool="browser_press_key")
async def _press_key(key: str, handle: str, purpose: str, expect: str,
                     tab: int, runtime: ToolRuntime) -> dict:
    return await _run("press", runtime.context,
                      key=key, handle=handle or None,
                      purpose=purpose or None, expect=expect or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_press_key(key: str, handle: str = "", purpose: str = "",
                            expect: str = "", tab: int = 0,
                            require_user_auth: bool = True, approval_reason: str = "", *,
                            runtime: ToolRuntime) -> str:
    """Press a keyboard key, optionally focused on a specific element.

    Accepts named keys (Enter, Tab, Escape, Backspace, ArrowUp, ArrowDown,
    ArrowLeft, ArrowRight, Home, End, PageUp, PageDown, Space) or a single
    character. Pass `handle` to focus an element first — for example, press Enter
    on a field to submit a search or form. If the key triggers a navigation or
    page change, set `expect`.
    e.g. Tab through form fields in sequence; Escape to dismiss a modal or autocomplete
    dropdown; ArrowDown to move focus within a custom listbox or date picker.

    Args:
        key: the key to press (named key or single character).
        handle: element handle to focus before pressing.
        purpose: short label for why this action is performed (for logging).
        expect: CSS selector to wait for after the key press.
        tab: the tab to act on (0 = controlled tab).
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = "Key pressed." and whether the expected element appeared.
    """
    return await _press_key(key, handle, purpose, expect, tab, runtime)


ACT_TOOLS = [
    browser_click, browser_type, browser_select_option,
    browser_press_key,
]
