"""Platform MCP browser read tools — observe without mutating page state.

Navigation, snapshots, element reads, screenshots, scrolling, waiting, and tab
management are all non-mutating with respect to the page's form state.

Shared params across all browser tools:
- `tab` (int, optional): which tab to act on — a stable tab id returned by
  browser_start_session or browser_tab. 0 (default) = the currently controlled tab.
- `handle` (str): an element reference returned by browser_snapshot or
  browser_query; pass it to read/act tools to target a specific element.
"""
from __future__ import annotations

import json as _json

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from vibecanvas_api.agents.tools.decorator import tool_output, ToolError
from vibecanvas_api.agents.tools.render import register_render, Rendered
from vibecanvas_api.services.platform_mcp.browser_tools._common import (
    _browser_health_lines,
    _run,
)


# ── browser_navigate ──────────────────────────────────────────────────────────

@register_render("browser_navigate")
def _render_navigate(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    title = data.get("title") or "(no title)"
    url = data.get("final_url") or ""
    settled = data.get("settled", True)
    content = f"Navigated to {title!r} ({url})"
    if not settled:
        content += " — load stopped before completion; partial page available"
    return Rendered(content=content, content_type="text/plain",
                    abstract=f"browser_navigate → {title!r}")


@tool_output(content_type="text/plain", tool="browser_navigate")
async def _navigate(url: str, wait_until: str, timeout_ms: int, tab: int,
                    runtime: ToolRuntime) -> dict:
    return await _run("navigate", runtime.context,
                      url=url, wait_until=wait_until, timeout=timeout_ms, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_navigate(url: str, wait_until: str = "load", timeout_ms: int = 15000,
                           tab: int = 0, require_user_auth: bool = True,
                           approval_reason: str = "", *,
                           runtime: ToolRuntime) -> str:
    """Navigate a tab to a URL and wait until the page is ready.

    e.g. open a product detail page before reading its price and description;
    navigate to a search results page to begin extracting a list of items.

    Args:
        url: the URL to navigate to.
        wait_until: readiness signal — "load" (default, waits for all resources) or
            "domcontentloaded" (waits for HTML only, faster).
        timeout_ms: max milliseconds to wait; loading is stopped at the limit and
            whatever rendered so far is returned.
        tab: the tab to navigate (0 = controlled tab).
        require_user_auth: whether to ask the user before executing. Defaults true.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = the page title, final URL, and whether loading fully settled.
    """
    return await _navigate(url, wait_until, timeout_ms, tab, runtime)


# ── browser_snapshot ──────────────────────────────────────────────────────────

@register_render("browser_snapshot")
def _render_snapshot(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    tab_id = data.get("tab") or raw.get("tab")
    dom = data.get("dom") or ""
    handles = data.get("handles") or []
    n = len(handles)
    lines: list[str] = []
    if tab_id is not None:
        lines.append(f"Tab: {tab_id} (use this tab with every handle below)")
    if dom:
        lines.append(str(dom).strip())
    if handles:
        lines.append("")
        lines.append("Interactive elements:")
        for item in handles[:80]:
            if not isinstance(item, dict):
                continue
            handle = str(item.get("handle") or "").strip()
            role = str(item.get("role") or item.get("tag") or "element").strip()
            name = str(item.get("name") or "").strip().replace("\n", " ")
            selector = str(item.get("selector") or item.get("css") or "").strip()
            if not handle:
                continue
            label = f'[{handle}] {role}'
            if name:
                label += f' "{name}"'
            if selector:
                label += f" selector={selector}"
            lines.append(label)
        if n > 80:
            lines.append(f"... {n - 80} more interactive elements omitted; use browser_query or scoped browser_snapshot.")
    content = "\n".join(lines).strip()
    abstract = (
        f"Snapshot{f' tab {tab_id}' if tab_id is not None else ''}: "
        f"{n} interactive element{'s' if n != 1 else ''}"
    )
    return Rendered(content=content, content_type="text/plain", abstract=abstract)


@tool_output(content_type="text/plain", tool="browser_snapshot")
async def _snapshot(scope: str, prune: bool, tab: int, runtime: ToolRuntime) -> dict:
    return await _run("snapshot", runtime.context,
                      scope=scope or None, prune=prune, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_snapshot(scope: str = "", prune: bool = True, tab: int = 0, *,
                           runtime: ToolRuntime) -> str:
    """Read the visible structure of the page as a compact element tree.

    Returns interactive elements (buttons, links, inputs, images, etc.) with their
    role, name, and a `handle` that can be used to act on or read that element.
    e.g. scan a checkout form to identify all its field labels before filling them;
    survey a navigation menu to find the link to a specific section; check what
    buttons are available on a confirmation dialog before deciding which to click.
    Use `scope` to focus on one region of a complex page and reduce noise.

    Args:
        scope: optional CSS selector to limit the snapshot to a page region.
        prune: True (default) to omit invisible and inert elements.
        tab: the tab to snapshot (0 = controlled tab).

    Returns:
        content = the element tree; each element shows its handle, role, and name.
    """
    return await _snapshot(scope, prune, tab, runtime)


# ── browser_read_text ─────────────────────────────────────────────────────────

@register_render("browser_read_text")
def _render_read_text(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    tab_id = data.get("tab") or raw.get("tab")
    text = data.get("text") or ""
    truncated = data.get("truncated", False)
    if truncated:
        text += "\n…[truncated; increase max_chars or use a query/selector to narrow the read]"
    if tab_id is not None:
        text = f"Tab: {tab_id}\n{text}"
    abstract = (
        f"Read text{f' tab {tab_id}' if tab_id is not None else ''}: {len(text)} chars"
        + (" (truncated)" if truncated else "")
    )
    return Rendered(content=text, content_type="text/plain", abstract=abstract)


@tool_output(content_type="text/plain", tool="browser_read_text")
async def _read_text(selector: str, handle: str, query: str, context: int,
                     max_chars: int, tab: int, runtime: ToolRuntime) -> dict:
    return await _run("read_text", runtime.context,
                      selector=selector or None, handle=handle or None,
                      query=query or None, context=context, max_chars=max_chars,
                      tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_read_text(selector: str = "", handle: str = "", query: str = "",
                            context: int = 200, max_chars: int = 8000, tab: int = 0, *,
                            runtime: ToolRuntime) -> str:
    """Read visible plain text from the page. Three modes:
    - `handle` / `selector` — text of that specific element.
      e.g. read the price shown in a product card, or the message in an error banner.
    - `query` — return only text snippets around each match (± `context` chars).
      e.g. query="cancellation policy" on a long terms page returns just the relevant paragraph.
    - neither — the full page text, capped at `max_chars`.
      e.g. read a short article or an order confirmation page in full.

    Args:
        selector: CSS selector to read text from a specific element.
        handle: element handle targeting a specific element.
        query: keyword to search; returns surrounding snippets instead of the whole page.
        context: chars of surrounding context per `query` match (default 200).
        max_chars: maximum characters to return (default 8000).
        tab: the tab to read (0 = controlled tab).

    Returns:
        content = the visible text, or keyword-sliced snippets when `query` is set.
    """
    return await _read_text(selector, handle, query, context, max_chars, tab, runtime)


# ── browser_query ─────────────────────────────────────────────────────────────

@register_render("browser_query")
def _render_query(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    tab_id = data.get("tab") or raw.get("tab")
    handles = data.get("handles") or []
    lines = [f"Tab: {tab_id} (use this tab with every handle below)"] if tab_id is not None else []
    for i, h in enumerate(handles, 1):
        role = h.get("role") or ""
        name = h.get("name") or ""
        handle = h.get("handle") or "?"
        css = h.get("css") or ""
        line = f"[{i}] handle={handle!r}  role={role!r}  name={name!r}"
        if css:
            line += f"  css={css!r}"
        lines.append(line)
    content = "\n".join(lines) if lines else "(no matches)"
    n = data.get("count", len(handles))
    abstract = f"Query{f' tab {tab_id}' if tab_id is not None else ''}: {n} match{'es' if n != 1 else ''}"
    return Rendered(content=content, content_type="text/plain", abstract=abstract)


@tool_output(content_type="text/plain", tool="browser_query")
async def _query(selector: str, role: str, name: str, text: str, tab: int,
                 runtime: ToolRuntime) -> dict:
    return await _run("query", runtime.context,
                      selector=selector or None, role=role or None,
                      name=name or None, text=text or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_query(selector: str = "", role: str = "", name: str = "",
                        text: str = "", tab: int = 0, *, runtime: ToolRuntime) -> str:
    """Find a specific element by its visible properties — a targeted lookup.

    Use this when you know what you're looking for and want a direct handle to act
    on it. Match by visible `text` (a substring of the label), `role` (e.g. "button",
    "link", "checkbox"), exact `name`, or a standard CSS `selector`. Each match
    includes a `handle` for immediate use and a stable `css` selector for recording
    into workflow nodes (handles are session-only; css selectors persist).
    e.g. role="button" text="Add to cart" to find the purchase button; selector="table
    tbody tr" to list every row in a data table; role="img" to locate all images.

    Args:
        selector: standard CSS selector.
        role: element role (e.g. "button", "link", "textbox").
        name: exact accessible name.
        text: substring of the visible label.
        tab: the tab to search (0 = controlled tab).

    Returns:
        content = one match per line with its handle, role, name, and css selector.
    """
    return await _query(selector, role, name, text, tab, runtime)


# ── browser_get_attribute ─────────────────────────────────────────────────────

@register_render("browser_get_attribute")
def _render_get_attribute(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    content = _json.dumps(data, ensure_ascii=False, indent=2)
    return Rendered(content=content, content_type="application/json",
                    abstract="browser_get_attribute")


@tool_output(content_type="application/json", tool="browser_get_attribute")
async def _get_attribute(handle: str, name: str, tab: int, runtime: ToolRuntime) -> dict:
    return await _run("get_attribute", runtime.context,
                      handle=handle, name=name or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_get_attribute(handle: str, name: str = "", tab: int = 0, *,
                                runtime: ToolRuntime) -> str:
    """Read one or all HTML attributes of an element.

    e.g. read the `href` of a link to get its destination URL; read the `value`
    of a hidden input field; omit `name` to inspect all attributes of an image
    or a custom data widget.

    Args:
        handle: element handle targeting the element.
        name: the attribute name to read; omit to get all attributes.
        tab: the tab to act on (0 = controlled tab).

    Returns:
        content = the attribute value(s) as JSON.
    """
    return await _get_attribute(handle, name, tab, runtime)


# ── browser_get_html ──────────────────────────────────────────────────────────

@register_render("browser_get_html")
def _render_get_html(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    value = data.get("value") or ""
    fmt = data.get("format", "html")
    truncated = data.get("truncated", False)
    if truncated:
        value += "\n…[truncated]"
    ct = {"markdown": "text/markdown", "html": "text/html"}.get(fmt, "text/plain")
    abstract = f"browser_get_html ({fmt}): {len(value)} chars" + (" (truncated)" if truncated else "")
    return Rendered(content=value, content_type=ct, abstract=abstract)


@tool_output(content_type="text/plain", tool="browser_get_html")
async def _get_html(handle: str, selector: str, format: str, max_chars: int,
                    tab: int, runtime: ToolRuntime) -> dict:
    return await _run("get_html", runtime.context,
                      handle=handle or None, selector=selector or None,
                      format=format, max_chars=max_chars, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_get_html(handle: str = "", selector: str = "", format: str = "html",
                           max_chars: int = 12000, tab: int = 0, *, runtime: ToolRuntime) -> str:
    """Read an element's content in the specified format (defaults to the whole page).

    `format` options:
    - "markdown" — compact, readable format preserving headings, links, and lists.
      e.g. reading a documentation page or a blog post to reason about its structure.
    - "text" — plain text, no markup.
    - "html" — raw HTML markup, for when you need exact attributes or structure.
      e.g. extracting `data-*` attributes set by scripts, reading hidden field values,
      or parsing the precise layout of a pricing table.

    Scope with a `handle` or `selector` to limit output size. Always capped at `max_chars`.

    Args:
        handle: element handle to scope the read.
        selector: CSS selector to scope the read.
        format: "markdown" | "text" | "html" (default "html").
        max_chars: maximum characters to return (default 12000).
        tab: the tab to read (0 = controlled tab).

    Returns:
        content = the element content in the requested format.
    """
    return await _get_html(handle, selector, format, max_chars, tab, runtime)


# ── browser_take_screenshot ───────────────────────────────────────────────────

@register_render("browser_take_screenshot")
def _render_screenshot(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    media = raw.get("media") or []
    media_path = ""
    if isinstance(media, list):
        for item in media:
            if isinstance(item, dict) and item.get("path"):
                media_path = str(item.get("path") or "")
                break
    path = data.get("screenshot") or data.get("image") or media_path
    content = f"Screenshot saved: {path}" if path else "Screenshot captured."
    return Rendered(content=content, content_type="text/plain",
                    abstract=f"Screenshot → {path or '?'}",
                    auxiliary=media if isinstance(media, list) else None)


@tool_output(content_type="text/plain", tool="browser_take_screenshot")
async def _take_screenshot(handle: str, scope: str, full_page: bool,
                           tab: int, runtime: ToolRuntime) -> dict:
    if handle:
        return await _run("get_image", runtime.context, handle=handle, tab=tab or None)
    return await _run("screenshot", runtime.context,
                      scope=scope or None, full_page=full_page, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_take_screenshot(handle: str = "", scope: str = "",
                                  full_page: bool = False, tab: int = 0, *,
                                  runtime: ToolRuntime) -> str:
    """Capture a screenshot of the page or a specific element.

    With `handle`, captures that element only. With `scope`, captures a named
    region. Otherwise captures the visible viewport (or the full scrollable page
    if `full_page=True`).
    e.g. capture a chart rendered on the page as a visual reference; take a
    full-page screenshot to verify the layout after a sequence of actions; capture
    a specific image element to inspect its rendered appearance.

    Args:
        handle: element handle to capture that element specifically.
        scope: named region to capture.
        full_page: True to capture the full scrollable page (default False, viewport only).
        tab: the tab to screenshot (0 = controlled tab).

    Returns:
        content = the path where the screenshot was saved.
    """
    return await _take_screenshot(handle, scope, full_page, tab, runtime)


# ── browser_scroll ────────────────────────────────────────────────────────────

@register_render("browser_scroll")
def _render_scroll(raw: dict, ctx) -> Rendered:
    return Rendered(content="Scrolled.", content_type="text/plain",
                    abstract="browser_scroll")


@tool_output(content_type="text/plain", tool="browser_scroll")
async def _scroll(handle: str, tab: int, runtime: ToolRuntime) -> dict:
    return await _run("scroll", runtime.context, handle=handle or None, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_scroll(handle: str = "", tab: int = 0, *, runtime: ToolRuntime) -> str:
    """Scroll the page, or scroll a specific element into view.

    e.g. scroll an infinite-scroll feed to reveal more items before reading them;
    bring an off-screen element into view before interacting with it.

    Args:
        handle: element handle to scroll into view; omit to scroll the page.
        tab: the tab to scroll (0 = controlled tab).

    Returns:
        content = confirmation that the scroll was performed.
    """
    return await _scroll(handle, tab, runtime)


# ── browser_wait_for ──────────────────────────────────────────────────────────

@register_render("browser_wait_for")
def _render_wait_for(raw: dict, ctx) -> Rendered:
    return Rendered(content="Condition met.", content_type="text/plain",
                    abstract="browser_wait_for → met")


@tool_output(content_type="text/plain", tool="browser_wait_for")
async def _wait_for(selector: str, text: str, timeout_ms: int, tab: int,
                    runtime: ToolRuntime) -> dict:
    return await _run("wait_for", runtime.context,
                      selector=selector or None, text=text or None,
                      timeout=timeout_ms, tab=tab or None)


@tool(response_format="content_and_artifact")
async def browser_wait_for(selector: str = "", text: str = "", timeout_ms: int = 8000,
                           tab: int = 0, *, runtime: ToolRuntime) -> str:
    """Wait until a CSS selector or visible text appears on the page.

    Use this after actions that trigger content to load dynamically. Prefer `text`
    when you're unsure of the exact element structure — it matches any substring of
    the page's visible text. Returns an error if the condition is not met within
    `timeout_ms`.
    e.g. wait for a results list to appear after submitting a search query; wait for
    "Payment confirmed" text after a checkout form is submitted.

    Args:
        selector: CSS selector to wait for.
        text: visible text substring to wait for.
        timeout_ms: max milliseconds to wait (default 8000).
        tab: the tab to observe (0 = controlled tab).

    Returns:
        content = "Condition met." when the selector or text appears.
    """
    return await _wait_for(selector, text, timeout_ms, tab, runtime)


# ── browser_tab ───────────────────────────────────────────────────────────────

@register_render("browser_tab")
def _render_tab(raw: dict, ctx) -> Rendered:
    data = raw.get("data") or {}
    tabs = data.get("tabs")
    if tabs is not None:
        lines = []
        if "controlled" in data:
            lines.append(
                f"Browser control: {'active' if data.get('controlled') else 'inactive'}"
            )
        for t in tabs:
            tab_id = t.get("tab") or t.get("id") or "?"
            title = t.get("title") or "(no title)"
            url = t.get("url") or ""
            suffix = " [active]" if t.get("active") else ""
            lines.append(f"[{tab_id}] {title!r}  {url}{suffix}")
        content = "\n".join(lines) if lines else "(no tabs)"
        health_lines = _browser_health_lines(data.get("health"))
        if health_lines:
            content = f"{content}\n" + "\n".join(health_lines)
        n = data.get("count", len(tabs))
        abstract = f"browser_tab: {n} tab{'s' if n != 1 else ''}"
    elif "tab" in data:
        tab_id = data["tab"]
        lines = [f"Tab: {tab_id}"]
        if data.get("url"):
            lines.append(f"URL: {data.get('url')}")
        if data.get("controlled") is not None:
            lines.append(f"Controlled: {bool(data.get('controlled'))}")
        content = "\n".join(lines)
        abstract = f"browser_tab → tab {tab_id}"
    else:
        content = "Done."
        abstract = "browser_tab"
    return Rendered(content=content, content_type="text/plain", abstract=abstract)


@tool_output(content_type="text/plain", tool="browser_tab")
async def _tab(action: str, tab: int, timeout_ms: int, runtime: ToolRuntime) -> dict:
    ctx = runtime.context
    if action == "list":
        return await _run("list_tabs", ctx)
    if action == "switch":
        return await _run("switch_tab", ctx, tab=tab or None)
    if action == "close":
        return await _run("close_tab", ctx, tab=tab or None)
    if action == "wait_new":
        return await _run("wait_for_new_tab", ctx, timeout=timeout_ms)
    if action == "list_open":
        return await _run("list_open_tabs", ctx)
    if action == "use":
        return await _run("use_tab", ctx, tab=tab or None)
    raise ToolError("bad_action",
                    f"unknown action {action!r}; valid: list|switch|close|wait_new|list_open|use")


@tool(response_format="content_and_artifact")
async def browser_tab(action: str, tab: int = 0, timeout_ms: int = 8000,
                      require_user_auth: bool = True, approval_reason: str = "", *,
                      runtime: ToolRuntime) -> str:
    """Manage browser tabs.

    e.g. a "Pay now" button opens a payment portal in a new tab — use wait_new to
    capture the new tab's id, then switch to it to continue the flow. Use list_open
    to see what the user currently has open and take control of a relevant page.

    `action`:
    - "list" — report current control status and list controlled tabs with URL,
      title, and active state. It is safe before a session starts.
    - "switch" — bring `tab` to the foreground.
    - "close" — close `tab` (cannot close the root controlled tab).
    - "wait_new" — wait up to `timeout_ms` for a new tab to open; returns its stable id.
    - "list_open" — list all tabs the user currently has open in this side panel's
      browser window. Before a browser session exists, use this to choose a tab,
      then call browser_start_session(target="existing", tab=<id>).
    - "use" — with an active browser session only, adopt an existing tab in the same
      browser window. It is not the pre-session attach path; starting control over
      an existing user tab must go through browser_start_session(target="existing").

    Args:
        action: the operation to perform (see above).
        tab: target tab id for switch / close / use (0 = controlled tab).
        timeout_ms: timeout for wait_new (default 8000).
        require_user_auth: whether to ask the user before mutating tab state.
            Defaults true. Read-only actions such as list/list_open/wait_new do not
            require approval.
        approval_reason: concise user-facing reason shown if approval is requested.

    Returns:
        content = tab list, new tab id, or confirmation depending on action.
    """
    return await _tab(action, tab, timeout_ms, runtime)


READ_TOOLS = [
    browser_navigate, browser_snapshot, browser_read_text,
    browser_query, browser_get_attribute, browser_get_html,
    browser_take_screenshot, browser_scroll, browser_wait_for,
    browser_tab,
]
