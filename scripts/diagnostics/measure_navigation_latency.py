"""Measure user-visible navigation latency across the whole application.

The probe is intentionally read-only.  It signs in through the normal UI,
clicks every visible top-level navigation item, exercises visible sub-tabs,
opens existing detail pages when fixtures are available, and samples several
historical Chats.  It reports three product-facing milestones:

* ``route_ms``: click -> React Router committed the destination URL;
* ``shell_ms``: click -> the destination's stable interactive shell appeared;
* ``settled_ms``: click -> route skeletons/loading placeholders disappeared.

API durations are captured separately so a slow chunk/render is not mistaken
for a slow backend. Credentials come from ``VIBECANVAS_PERF_EMAIL`` and
``VIBECANVAS_PERF_PASSWORD`` and default to the local shared test account.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

from playwright.sync_api import Locator, Page, Request, Response, TimeoutError, sync_playwright


@dataclass
class RequestMetric:
    method: str
    path: str
    status: int
    duration_ms: int
    surface: str


@dataclass(frozen=True)
class Surface:
    name: str
    path: str
    ready_selector: str


TOP_LEVEL_SURFACES = (
    Surface("Chat", "/chat", '[data-role="agent-composer-input"]'),
    Surface("Workflow", "/workspace", '[data-testid="wf-search"]'),
    Surface("Task", "/tasks", '[data-testid="tasks-new-task-trigger"]'),
    Surface("Deployment", "/deployments", '#main-content h1:has-text("Deployment")'),
    Surface("API Key", "/credentials", '[data-testid="cred-add-button"]'),
    Surface("MCP Server", "/mcp-servers", '[data-testid="mcp-add-button"]'),
    Surface("Skill", "/skills", '#main-content h1:has-text("Skill")'),
    Surface("Knowledge", "/knowledge", '#main-content h1:has-text("Knowledge")'),
    Surface("Storage", "/storage", '#main-content input[placeholder="Search current folder"]'),
)


def _milliseconds(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _visible(locator: Locator) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _wait_for_settled(page: Page, timeout_ms: int = 8_000) -> None:
    """Wait for route-local loading UI, never for global network-idle.

    Tasks and account usage legitimately poll, while run/event surfaces may
    keep SSE open.  Network-idle therefore overstates perceived latency and
    can hang forever.  The visible loading contract is the correct UX gate.
    """

    page.wait_for_function(
        """
        () => {
          const root = document.querySelector('#main-content');
          if (!root) return false;
          const visible = (node) => {
            const style = window.getComputedStyle(node);
            const box = node.getBoundingClientRect();
            return style.display !== 'none' && style.visibility !== 'hidden'
              && box.width > 0 && box.height > 0;
          };
          const pending = root.querySelectorAll(
            '[aria-busy="true"], .animate-pulse, [data-loading="true"]'
          );
          if (Array.from(pending).some(visible)) return false;
          const loadingText = /^(Loading(?:\u2026|\.\.\.)?|载入中|加载中|正在加载)/i;
          return !Array.from(root.querySelectorAll('[role="status"], .empty-state'))
            .filter(visible)
            .some((node) => loadingText.test((node.textContent || '').trim()));
        }
        """,
        timeout=timeout_ms,
    )


def _click_surface(page: Page, surface: Surface, active_surface: list[str]) -> dict:
    target = page.locator(f'a[href$="{surface.path}"]').first
    if target.count() == 0:
        return {"name": surface.name, "path": surface.path, "skipped": "navigation hidden"}

    # AppSidebar preloads a route module on pointer intent.  Give that behavior
    # the small, realistic lead time a physical pointer normally creates, but
    # start the user-visible stopwatch at the click itself.
    target.hover()
    page.wait_for_timeout(80)
    active_surface[0] = surface.name
    started = time.perf_counter()
    target.click()
    page.wait_for_url(f"**{surface.path}")
    route_ms = _milliseconds(started)
    page.locator(surface.ready_selector).first.wait_for(state="visible", timeout=15_000)
    shell_ms = _milliseconds(started)
    settled_error = None
    try:
        _wait_for_settled(page)
    except TimeoutError:
        settled_error = "visible loading state remained after 8s"
    result = {
        "name": surface.name,
        "path": surface.path,
        "route_ms": route_ms,
        "shell_ms": shell_ms,
        "settled_ms": _milliseconds(started),
    }
    if settled_error:
        result["warning"] = settled_error
    return result


def _exercise_tabs(page: Page, owner: str, active_surface: list[str]) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = set()
    # Re-query each loop: some tabs (Deployment language examples) are nested
    # and only materialize after their parent tab becomes active.
    for _pass in range(3):
        snapshots = page.locator('#main-content [role="tab"]:visible').evaluate_all(
            """nodes => nodes.map((node) => ({
              testid: node.getAttribute('data-testid') || '',
              label: (node.textContent || '').trim(),
              value: node.getAttribute('value') || ''
            }))"""
        )
        pending: list[tuple[str, str, str]] = []
        for snapshot in snapshots:
            label = str(snapshot.get("label") or snapshot.get("testid") or "").strip()
            testid = str(snapshot.get("testid") or "")
            key = f"{testid}:{label}:{snapshot.get('value') or ''}"
            if label and key not in seen:
                seen.add(key)
                pending.append((label, testid, key))
        if not pending:
            break
        for label, testid, _key in pending:
            tab = (
                page.locator(f'#main-content [data-testid="{testid}"]')
                if testid
                else page.locator('#main-content [role="tab"]:visible').filter(has_text=label)
            ).first
            active_surface[0] = f"{owner} / {label}"
            started = time.perf_counter()
            try:
                tab.click(timeout=3_000)
                deadline = time.perf_counter() + 5
                while time.perf_counter() < deadline:
                    if (
                        tab.get_attribute("aria-selected") == "true"
                        or tab.get_attribute("data-state") == "active"
                    ):
                        break
                    page.wait_for_timeout(20)
                else:
                    raise TimeoutError("tab did not become selected after 5s")
                selected_ms = _milliseconds(started)
                try:
                    _wait_for_settled(page, timeout_ms=4_000)
                    warning = None
                except TimeoutError:
                    warning = "visible loading state remained after 4s"
                result = {
                    "owner": owner,
                    "tab": label,
                    "selected_ms": selected_ms,
                    "settled_ms": _milliseconds(started),
                }
                if warning:
                    result["warning"] = warning
                results.append(result)
            except Exception as exc:
                results.append({"owner": owner, "tab": label, "error": str(exc)[:240]})
    return results


def _first_href(page: Page, pattern: str) -> str | None:
    candidates = page.locator(f'#main-content a[href*="{pattern}"]')
    for index in range(candidates.count()):
        href = candidates.nth(index).get_attribute("href")
        if not href:
            continue
        path = urlsplit(href).path
        if path.rstrip("/") != pattern.rstrip("/"):
            return path
    return None


def _open_detail(
    page: Page,
    *,
    owner: str,
    list_path: str,
    detail_path: str | None,
    active_surface: list[str],
) -> tuple[dict, list[dict]]:
    if not detail_path:
        return ({"name": owner, "skipped": "no existing item"}, [])
    active_surface[0] = owner
    started = time.perf_counter()
    current = urlsplit(page.url)
    origin = f"{current.scheme}://{current.netloc}"
    try:
        page.goto(f"{origin}{detail_path}", wait_until="domcontentloaded")
        page.wait_for_url(f"**{detail_path}")
        route_ms = _milliseconds(started)
        page.locator('#main-content h1').first.wait_for(state="visible", timeout=15_000)
    except Exception as exc:
        return ({
            "name": owner,
            "path": detail_path,
            "error": str(exc)[:500],
        }, [])
    shell_ms = _milliseconds(started)
    warning = None
    try:
        _wait_for_settled(page)
    except TimeoutError:
        warning = "visible loading state remained after 8s"
    result = {
        "name": owner,
        "path": detail_path,
        "route_ms": route_ms,
        "shell_ms": shell_ms,
        "settled_ms": _milliseconds(started),
    }
    if warning:
        result["warning"] = warning
    return result, _exercise_tabs(page, owner, active_surface)


def _measure_chat_history(page: Page, active_surface: list[str]) -> list[dict]:
    page.goto(f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}/chat", wait_until="domcontentloaded")
    page.locator('[data-role="agent-composer-input"]').wait_for(state="visible", timeout=15_000)
    rows = page.locator('[data-chat-id]:not([data-chat-id=""])')
    results: list[dict] = []
    for index in range(min(rows.count(), 5)):
        row = page.locator('[data-chat-id]:not([data-chat-id=""])').nth(index)
        chat_id = row.get_attribute("data-chat-id")
        if not chat_id:
            continue
        active_surface[0] = "Chat history"
        row.hover()
        page.wait_for_timeout(80)
        started = time.perf_counter()
        row.click()
        composer = page.locator('[data-role="agent-composer-input"]')
        composer.wait_for(state="visible", timeout=10_000)
        shell_ms = _milliseconds(started)
        message = page.locator('[data-role="agent-message-list"] [data-chat-render-key]').first
        try:
            message.wait_for(state="visible", timeout=10_000)
            message_ms = _milliseconds(started)
            warning = None
        except TimeoutError:
            message_ms = None
            warning = "no rendered message after 10s"
        result = {
            "chat_id": chat_id,
            "composer_ms": shell_ms,
            "recent_message_ms": message_ms,
        }
        if warning:
            result["warning"] = warning
        results.append(result)
    return results


def main() -> None:
    base_url = os.getenv("VIBECANVAS_PERF_BASE_URL", "http://[::1]:9001").rstrip("/")
    email = os.getenv("VIBECANVAS_PERF_EMAIL", "test")
    password = os.getenv("VIBECANVAS_PERF_PASSWORD", "test")
    metrics: list[RequestMetric] = []
    request_started: dict[Request, float] = {}
    active_surface = ["bootstrap"]

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--proxy-server=direct://", "--proxy-bypass-list=*"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        context.add_init_script("localStorage.setItem('vibecanvas.locale', 'en')")
        page = context.new_page()
        page.set_default_timeout(5_000)

        def on_request(request: Request) -> None:
            if "/api/" in request.url:
                request_started[request] = time.perf_counter()

        def on_response(response: Response) -> None:
            started = request_started.pop(response.request, None)
            if started is None:
                return
            metrics.append(
                RequestMetric(
                    method=response.request.method,
                    path=urlsplit(response.url).path,
                    status=response.status,
                    duration_ms=round((time.perf_counter() - started) * 1000),
                    surface=active_surface[0],
                )
            )

        page.on("request", on_request)
        page.on("response", on_response)
        page.goto(f"{base_url}/login", wait_until="domcontentloaded")
        page.locator("#login-email").fill(email)
        page.locator("#login-password").fill(password)
        page.locator('form button[type="submit"]').click()
        page.wait_for_url("**/chat", timeout=15_000)
        page.locator('[data-role="agent-composer-input"]').wait_for(state="visible", timeout=15_000)

        top_level: list[dict] = []
        list_tabs: list[dict] = []
        detail_candidates: dict[str, str | None] = {}
        for surface in TOP_LEVEL_SURFACES:
            print(f"measuring {surface.name}", file=sys.stderr, flush=True)
            result = _click_surface(page, surface, active_surface)
            top_level.append(result)
            if "skipped" not in result:
                list_tabs.extend(_exercise_tabs(page, surface.name, active_surface))
                if surface.path in {"/tasks", "/deployments", "/knowledge", "/skills", "/mcp-servers"}:
                    detail_candidates[surface.path] = _first_href(page, surface.path)

        # Settings is reached through the user menu rather than the primary
        # navigation rail, but it is still a full product surface.
        active_surface[0] = "Settings"
        print("measuring Settings", file=sys.stderr, flush=True)
        settings_started = time.perf_counter()
        page.goto(f"{base_url}/settings", wait_until="domcontentloaded")
        page.locator('[data-testid="settings-tab-preferences"]').wait_for(state="visible", timeout=15_000)
        settings_shell_ms = _milliseconds(settings_started)
        try:
            _wait_for_settled(page)
            settings_warning = None
        except TimeoutError:
            settings_warning = "visible loading state remained after 8s"
        settings_result = {
            "name": "Settings",
            "path": "/settings",
            "route_ms": settings_shell_ms,
            "shell_ms": settings_shell_ms,
            "settled_ms": _milliseconds(settings_started),
        }
        if settings_warning:
            settings_result["warning"] = settings_warning
        top_level.append(settings_result)
        list_tabs.extend(_exercise_tabs(page, "Settings", active_surface))

        details: list[dict] = []
        detail_tabs: list[dict] = []
        detail_specs = (
            ("Task detail", "/tasks"),
            ("Deployment detail", "/deployments"),
            ("Knowledge detail", "/knowledge"),
            ("Skill detail", "/skills"),
            ("MCP detail", "/mcp-servers"),
        )
        for owner, list_path in detail_specs:
            print(f"measuring {owner}", file=sys.stderr, flush=True)
            result, tabs = _open_detail(
                page,
                owner=owner,
                list_path=list_path,
                detail_path=detail_candidates.get(list_path),
                active_surface=active_surface,
            )
            details.append(result)
            detail_tabs.extend(tabs)

        # Platform MCP detail is always available even on a fresh account.
        result, tabs = _open_detail(
            page,
            owner="Platform MCP detail",
            list_path="/mcp-servers",
            detail_path="/mcp-servers/platform/knowledge",
            active_surface=active_surface,
        )
        details.append(result)
        detail_tabs.extend(tabs)

        print("measuring Chat history", file=sys.stderr, flush=True)
        chat_history = _measure_chat_history(page, active_surface)
        slow_requests = sorted(
            (asdict(metric) for metric in metrics),
            key=lambda item: item["duration_ms"],
            reverse=True,
        )[:30]
        result = {
            "base_url": base_url,
            "top_level": top_level,
            "list_tabs": list_tabs,
            "details": details,
            "detail_tabs": detail_tabs,
            "chat_history": chat_history,
            "slowest_api_requests": slow_requests,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
