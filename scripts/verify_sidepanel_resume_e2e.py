#!/usr/bin/env python3
"""Real side-panel UI, HITL, and reconnect/resume end-to-end verifier.

The test drives the unpacked MV3 extension exactly as a user-visible side panel:
the extension shell hosts `/embed/chat`, the React composer posts the Turn, the
Codex Runtime calls Browser MCP, and approval decisions are clicked in the UI.
Backend reads are assertions only; they never advance Runtime control flow.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import re
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import BrowserContext, Frame, Page, Request, async_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ID = "mkfldhmlgdbpmhplaphhcfcdcoaakcik"
MESSAGE_URL = re.compile(
    r"/api/v1/chat-scopes/(?P<scope>[^/]+)/chats/(?P<chat>[^/]+)/messages$"
)


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


class FixtureServer:
    def __init__(self, port: int) -> None:
        root = REPO_ROOT / "extension" / "test-fixtures"

        def handler(*args: object, **kwargs: object) -> _QuietHandler:
            return _QuietHandler(*args, directory=str(root), **kwargs)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> FixtureServer:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Api:
    def __init__(self, base_url: str) -> None:
        self.client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=90)
        self.token = ""

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self.token}"}

    async def login(self) -> None:
        response = await self.client.post(
            "/api/v1/auth/login",
            json={"email": "test", "password": "test"},
        )
        response.raise_for_status()
        self.token = response.json()["session_token"]

    async def get_json(self, path: str) -> Any:
        response = await self.client.get(path, headers=self.headers())
        response.raise_for_status()
        return response.json()

    async def delete(self, path: str) -> None:
        response = await self.client.delete(path, headers=self.headers())
        response.raise_for_status()

    async def post(self, path: str) -> None:
        response = await self.client.post(path, headers=self.headers())
        response.raise_for_status()

    async def close(self) -> None:
        await self.client.aclose()


async def wait_for_embed(panel: Page, console: list[str], timeout_s: float = 60) -> Frame:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for frame in panel.frames:
            if "/embed/chat" not in frame.url:
                continue
            if await frame.locator('[data-role="agent-composer-input"]').count():
                await frame.locator('[data-role="agent-composer-input"]').wait_for(
                    state="visible",
                    timeout=10_000,
                )
                return frame
        await asyncio.sleep(0.2)
    frame_dump = []
    for frame in panel.frames:
        body = ""
        with contextlib.suppress(Exception):
            body = (await frame.locator("body").inner_text())[:1_500]
        frame_dump.append({"url": frame.url, "body": body})
    raise AssertionError(
        "side-panel embed did not become ready\n"
        f"frames={json.dumps(frame_dump, ensure_ascii=False)}\n"
        f"console={json.dumps(console[-50:], ensure_ascii=False)}"
    )


async def chrome_tab_id(control_page: Page, fixture_url: str) -> int:
    return int(
        await control_page.evaluate(
            """async (url) => {
              const tabs = await chrome.tabs.query({});
              const tab = tabs.find((candidate) => candidate.url === url);
              if (!tab || typeof tab.id !== "number") throw new Error("fixture tab not found");
              return tab.id;
            }""",
            fixture_url,
        )
    )


async def set_approval_mode(frame: Frame, label: str) -> None:
    trigger = frame.locator('[data-role="chat-approval-mode-select"]')
    await trigger.wait_for(state="visible")
    deadline = time.monotonic() + 60
    while await trigger.is_disabled():
        if time.monotonic() >= deadline:
            raise AssertionError("approval select stayed disabled after chat recovery")
        await asyncio.sleep(0.1)
    labels = {
        "Always ask": re.compile(r"^(Always ask|始终询问)$"),
        "Always allow": re.compile(r"^(Always allow|始终允许)$"),
    }
    if label not in labels:
        raise AssertionError(f"unsupported approval label: {label}")
    listbox = frame.locator('[role="listbox"]:visible').last
    opened = False
    last_error: Exception | None = None
    for _ in range(5):
        try:
            await trigger.focus()
            await trigger.press("ArrowDown")
            await listbox.wait_for(state="visible", timeout=2_000)
            opened = True
            break
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.2)
    if not opened:
        state = await trigger.evaluate(
            """element => ({
              html: element.outerHTML,
              disabled: element.matches(':disabled'),
              expanded: element.getAttribute('aria-expanded'),
              bodyPointerEvents: getComputedStyle(document.body).pointerEvents,
            })"""
        )
        raise AssertionError(f"approval select did not open: {state!r}") from last_error
    option = listbox.get_by_role("option", name=labels[label])
    await option.wait_for(state="visible")
    await option.focus()
    await option.press("Enter")
    await listbox.wait_for(state="hidden")
    if not labels[label].search((await trigger.inner_text()).strip()):
        raise AssertionError(
            f"approval mode did not change to {label!r}: {await trigger.inner_text()!r}"
        )


async def send_and_capture(
    panel: Page,
    frame: Frame,
    content: str,
    network: list[str],
    *,
    keyboard_submit: bool = False,
) -> tuple[str, str, dict[str, Any]]:
    composer = frame.locator('[data-role="agent-composer-input"]')
    send = frame.locator('[data-action="agent-composer-send"]')
    after_click: dict[str, Any] = {}

    def is_target_turn(request: Request) -> bool:
        if request.method != "POST" or MESSAGE_URL.search(request.url) is None:
            return False
        try:
            payload = request.post_data_json
        except Exception:
            return False
        return isinstance(payload, dict) and payload.get("content") == content

    try:
        async with panel.expect_request(
            is_target_turn,
            timeout=30_000,
        ) as request_info:
            await composer.wait_for(state="visible")
            await send.wait_for(state="visible")
            await composer.fill(content)
            deadline = time.monotonic() + 10
            while await send.is_disabled():
                if time.monotonic() >= deadline:
                    raise AssertionError("Send stayed disabled after filling the composer")
                await asyncio.sleep(0.05)
            # Let the controlled textarea value and the button's onClick
            # closure land in the same React commit before simulating a human
            # pointer click.
            await asyncio.sleep(0.25)
            if keyboard_submit:
                await composer.press("Control+Enter")
            else:
                await send.click()
            await asyncio.sleep(0.1)
            after_click = {
                "value": await composer.input_value(),
                "actions": {
                    name: await frame.locator(
                        f'[data-action="agent-composer-{name}"]'
                    ).count()
                    for name in ("send", "stop", "retry")
                },
            }
    except Exception as exc:
        raise AssertionError(
            f"Turn POST was not observed; after_click={after_click!r}; "
            f"recent_post_requests={network[-20:]!r}"
        ) from exc
    request: Request = await request_info.value
    match = MESSAGE_URL.search(request.url)
    if match is None:
        raise AssertionError(f"unexpected Turn URL: {request.url}")
    payload = request.post_data_json
    if not isinstance(payload, dict):
        raise AssertionError(f"Turn body is not a JSON object: {payload!r}")
    return match.group("scope"), match.group("chat"), payload


async def wait_turn_idle(frame: Frame, timeout_s: float) -> None:
    await frame.locator('[data-action="agent-composer-send"]').wait_for(
        state="visible",
        timeout=timeout_s * 1_000,
    )
    await frame.locator('[data-role="agent-composer-input"]').wait_for(
        state="visible",
        timeout=10_000,
    )


async def pending_card(frame: Frame, tool_name: str, timeout_s: float = 90):
    card = frame.locator(
        '[data-role="interactive-artifact"]:has('
        '[data-action="interactive-submit"])'
    ).filter(has_text=tool_name).last
    await card.locator('[data-role="pre-tool-approval-body"]').wait_for(
        state="visible",
        timeout=timeout_s * 1_000,
    )
    await card.locator('[data-action="interactive-submit"]').wait_for(state="visible")
    return card


async def assert_one_user_bubble(frame: Frame, content: str) -> None:
    bubbles = frame.locator('[data-message-role="user"]')
    rendered = [
        re.sub(r"\s+", " ", text).strip()
        for text in await bubbles.all_inner_texts()
    ]
    expected = re.sub(r"\s+", " ", content).strip()
    # The command token is an input-side activation directive. Depending on
    # whether the live or persisted projection is currently rendered, the UI
    # may omit that leading token while preserving the user's actual request.
    projected = re.sub(r"^/browser\s+", "", expected)
    count = sum(text in {expected, projected} for text in rendered)
    if count != 1:
        raise AssertionError(
            "user message rendered "
            f"{count} times, expected exactly once; bubbles={rendered!r}"
        )


async def active_run(api: Api, scope_id: str, chat_id: str) -> dict[str, Any] | None:
    rows = await api.get_json(f"/api/v1/chat-scopes/{scope_id}/active-runs")
    return next((row for row in rows if row.get("chat_id") == chat_id), None)


async def wait_no_active_run(
    api: Api,
    scope_id: str,
    chat_id: str,
    timeout_s: float = 90,
) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if await active_run(api, scope_id, chat_id) is None:
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"chat {chat_id} still has an active run")


async def stop_waiting_browser_turn(
    *,
    panel: Page,
    frame: Frame,
    api: Api,
    scope_id: str,
    chat_id: str,
    tab_id: int,
    target: Page,
    network: list[str],
    console: list[str],
    timeout_s: float,
) -> None:
    before_clicks = await target.evaluate("window.__skeinixClickCount")
    stop_prompt = (
        f"Use browser_click with selector='#submit', tab={tab_id}, "
        "require_user_auth=true. Do not call another tool."
    )
    _, stop_chat, _ = await send_and_capture(
        panel,
        frame,
        stop_prompt,
        network,
        keyboard_submit=True,
    )
    if stop_chat != chat_id:
        raise AssertionError("side panel switched Chat before Stop")
    await pending_card(frame, "browser_click")
    stop_button = frame.locator('[data-action="agent-composer-stop"]')
    await stop_button.evaluate(
        """element => {
          window.__vibecanvasStopDomClicks = 0;
          window.__vibecanvasStopPointerDowns = 0;
          element.addEventListener(
            'pointerdown',
            () => { window.__vibecanvasStopPointerDowns += 1; },
            { capture: true },
          );
          element.addEventListener(
            'click',
            () => { window.__vibecanvasStopDomClicks += 1; },
            { capture: true },
          );
        }"""
    )
    try:
        async with panel.expect_request(
            lambda request: (
                request.method == "POST"
                and request.url.endswith(
                    f"/chats/{chat_id}/active-turn/cancel"
                )
            ),
            timeout=15_000,
        ):
            # The harness opens sidepanel.html as an ordinary extension tab.
            # Once chrome.debugger controls the fixture tab, headless Chromium
            # does not route Playwright's coordinate pointer input into the
            # cross-origin extension iframe. Focus + Enter remains a trusted,
            # user-level activation of the exact Stop button (and component
            # tests separately cover pointer activation).
            await panel.bring_to_front()
            await stop_button.press("Enter")
    except Exception as exc:
        button_state = await stop_button.evaluate(
            """element => ({
              html: element.outerHTML,
              clicks: window.__vibecanvasStopDomClicks,
              pointerdowns: window.__vibecanvasStopPointerDowns,
              disabled: element.matches(':disabled'),
              connected: element.isConnected,
              rect: element.getBoundingClientRect().toJSON(),
              hit: (() => {
                const rect = element.getBoundingClientRect();
                const hit = document.elementFromPoint(
                  rect.left + rect.width / 2,
                  rect.top + rect.height / 2,
                );
                return hit ? hit.outerHTML : null;
              })(),
              visibility: document.visibilityState,
            })"""
        )
        panel_state = {
            "visibility": await panel.evaluate("document.visibilityState"),
            "iframe_rect": await panel.locator("#embed").evaluate(
                "element => element.getBoundingClientRect().toJSON()"
            ),
        }
        raise AssertionError(
            "Stop did not submit a Chat-scoped cancel request; "
            f"button={button_state!r}; "
            f"panel={panel_state!r}; "
            f"console={console[-30:]!r}; "
            f"recent_requests={network[-30:]!r}"
        ) from exc
    await wait_turn_idle(frame, timeout_s)
    await wait_no_active_run(api, scope_id, chat_id, timeout_s)
    if await target.evaluate("window.__skeinixClickCount") != before_clicks:
        raise AssertionError("stopped UI Turn changed the page")


async def run(args: argparse.Namespace) -> None:
    extension_dist = REPO_ROOT / "extension" / "dist"
    chromium = Path(args.chromium) if args.chromium else None
    if not (extension_dist / "manifest.json").exists():
        raise RuntimeError("extension/dist is missing")
    if chromium is not None and not chromium.exists():
        raise RuntimeError(f"Chromium executable is missing: {chromium}")

    api = Api(args.base_url)
    context: BrowserContext | None = None
    profile = tempfile.TemporaryDirectory(prefix="vibecanvas-sidepanel-e2e-")
    chats_to_delete: set[tuple[str, str]] = set()
    console: list[str] = []
    network: list[str] = []
    fixture_url = f"http://127.0.0.1:{args.fixture_port}/page.html"
    try:
        await api.login()
        runtime = await api.get_json("/api/v1/agent-runtime/settings")
        if runtime.get("default_runtime_type") != "codex":
            raise AssertionError(f"default Runtime is not codex: {runtime!r}")
        capabilities = await api.get_json("/api/v1/agent-runtime/capabilities")
        if not capabilities.get("runtime_available") or capabilities.get("error_code"):
            raise AssertionError(f"Codex broker Runtime is unavailable: {capabilities!r}")

        with FixtureServer(args.fixture_port):
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    profile.name,
                    executable_path=str(chromium) if chromium else None,
                    headless=True,
                    viewport={"width": 430, "height": 900},
                    args=[
                        f"--disable-extensions-except={extension_dist}",
                        f"--load-extension={extension_dist}",
                        "--no-sandbox",
                    ],
                )
                target = context.pages[0]
                await target.goto(fixture_url, wait_until="domcontentloaded")
                await target.evaluate(
                    """() => {
                      window.__skeinixClickCount = 0;
                      const button = document.querySelector("#submit");
                      button.addEventListener("click", () => {
                        window.__skeinixClickCount += 1;
                        button.dataset.clicked = String(window.__skeinixClickCount);
                        button.textContent = `Submitted ${window.__skeinixClickCount}`;
                      });
                    }"""
                )

                worker = (
                    context.service_workers[0]
                    if context.service_workers
                    else await context.wait_for_event("serviceworker")
                )
                await worker.evaluate(
                    """async ({token}) => chrome.storage.local.set({
                      embedSessionToken: token,
                      embedAgentSettings: {
                        model_id: "gpt-5.6-luna",
                        reasoning_effort: "low",
                      },
                      lang: "en",
                      theme: "light",
                    })""",
                    {"token": api.token},
                )
                panel = await context.new_page()
                panel.on(
                    "console",
                    lambda message: console.append(
                        f"{message.type}: {message.text}"
                    ),
                )
                panel.on("pageerror", lambda error: console.append(f"pageerror: {error}"))
                panel.on(
                    "request",
                    lambda request: network.append(
                        f"{request.method} {request.url} {request.post_data or ''}"
                    )
                    if (
                        request.method == "POST"
                        or "/active-runs" in request.url
                        or request.url.endswith("/cancel")
                    )
                    else None,
                )
                await panel.goto(
                    f"chrome-extension://{EXTENSION_ID}/sidepanel.html",
                    wait_until="domcontentloaded",
                )
                frame = await wait_for_embed(panel, console)
                tab_id = await chrome_tab_id(panel, fixture_url)
                print(f"[ok] real side-panel iframe ready; fixture tab={tab_id}", flush=True)

                await set_approval_mode(frame, "Always allow")
                setup = (
                    f"/browser Call browser_start_session with target='existing', "
                    f"tab={tab_id}, require_user_auth=true. Then call "
                    f"browser_read_text with selector='#title', tab={tab_id}. "
                    "Do not modify the page. Reply with the exact title."
                )
                scope_id, chat_id, payload = await send_and_capture(
                    panel, frame, setup, network
                )
                chats_to_delete.add((scope_id, chat_id))
                if payload.get("surface") != "sidepanel":
                    raise AssertionError(f"wrong surface in UI Turn: {payload!r}")
                if payload.get("agent_surface") != "browser":
                    raise AssertionError(f"wrong agent_surface in UI Turn: {payload!r}")
                if payload.get("approval_mode") != "always_allow":
                    raise AssertionError(f"wrong approval_mode in UI Turn: {payload!r}")
                await frame.get_by_text("Review Item 42", exact=True).last.wait_for(
                    state="visible",
                    timeout=args.turn_timeout * 1_000,
                )
                await wait_turn_idle(frame, args.turn_timeout)
                await assert_one_user_bubble(frame, setup)
                print("[ok] UI sent sidepanel/browser policy and streamed one user Turn", flush=True)

                await set_approval_mode(frame, "Always ask")
                await panel.reload(wait_until="domcontentloaded")
                frame = await wait_for_embed(panel, console)
                approval_text = (
                    await frame.locator(
                        '[data-role="chat-approval-mode-select"]'
                    ).inner_text()
                ).strip()
                if not re.search(r"(Smart approval|智能授权)", approval_text):
                    raise AssertionError(
                        f"per-Turn approval mode should reset on reload: {approval_text!r}"
                    )
                await assert_one_user_bubble(frame, setup)
                await set_approval_mode(frame, "Always ask")
                print(
                    "[ok] completed Chat recovered; next Turn selected its own approval mode",
                    flush=True,
                )

                if args.stop_only:
                    await stop_waiting_browser_turn(
                        panel=panel,
                        frame=frame,
                        api=api,
                        scope_id=scope_id,
                        chat_id=chat_id,
                        tab_id=tab_id,
                        target=target,
                        network=network,
                        console=console,
                        timeout_s=args.turn_timeout,
                    )
                    print(
                        "[ok] side-panel Stop cancelled only the current waiting Turn",
                        flush=True,
                    )
                    return

                approve_prompt = (
                    f"Use browser_click exactly once with selector='#submit', tab={tab_id}, "
                    "require_user_auth=true. Do not call another mutating tool."
                )
                captured_scope, captured_chat, captured = await send_and_capture(
                    panel,
                    frame,
                    approve_prompt,
                    network,
                    keyboard_submit=True,
                )
                if (captured_scope, captured_chat) != (scope_id, chat_id):
                    raise AssertionError("side panel switched Chat before the HITL Turn")
                if captured.get("approval_mode") != "always_ask":
                    raise AssertionError(f"per-Turn approval mode was lost: {captured!r}")
                await pending_card(frame, "browser_click")
                if await target.evaluate("window.__skeinixClickCount") != 0:
                    raise AssertionError("browser click ran before the UI decision")
                run_before = await active_run(api, scope_id, chat_id)
                if not run_before or run_before.get("status") != "waiting_approval":
                    raise AssertionError(f"durable waiting run is missing: {run_before!r}")
                pending_id = run_before["pending_hitl"][0]["hitl_request_id"]
                print("[ok] approval card is standalone and the action is suspended", flush=True)

                await context.set_offline(True)
                await asyncio.sleep(1)
                await context.set_offline(False)
                await panel.reload(wait_until="domcontentloaded")
                frame = await wait_for_embed(panel, console)
                resumed = await pending_card(frame, "browser_click")
                run_after = await active_run(api, scope_id, chat_id)
                if not run_after or run_after["pending_hitl"][0]["hitl_request_id"] != pending_id:
                    raise AssertionError(
                        f"reload did not recover the same durable HITL: {run_after!r}"
                    )
                try:
                    await assert_one_user_bubble(frame, approve_prompt)
                except AssertionError as exc:
                    history = await api.get_json(
                        f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
                        "?limit=200"
                    )
                    screenshot = Path(args.screenshot).with_name(
                        "vibecanvas-sidepanel-pending-resume-failure.png"
                    )
                    await panel.screenshot(path=str(screenshot), full_page=True)
                    visible_text = (await frame.locator("body").inner_text())[:8_000]
                    raise AssertionError(
                        f"{exc}; durable_history={history!r}; "
                        f"visible_text={visible_text!r}; screenshot={screenshot}"
                    ) from exc
                print("[ok] offline/reload recovered the same Chat, Run, and HITL card", flush=True)

                await resumed.locator('[data-action="interactive-submit"]').click()
                await wait_turn_idle(frame, args.turn_timeout)
                await wait_no_active_run(api, scope_id, chat_id, args.turn_timeout)
                if await target.evaluate("window.__skeinixClickCount") != 1:
                    raise AssertionError("approved UI action did not execute exactly once")
                approved_card = frame.locator(
                    '[data-role="interactive-artifact"]'
                ).filter(has_text=re.compile(r"(Approved|已允许)")).last
                await approved_card.get_by_text(
                    re.compile(r"^(Approved|已允许)$")
                ).wait_for(state="visible")
                if await approved_card.locator('[data-action="interactive-submit"]').count():
                    raise AssertionError("resolved HITL remained interactive")
                print("[ok] UI approval resumed once and froze the resolved card", flush=True)

                deny_prompt = (
                    f"Use browser_click exactly once with selector='#submit', tab={tab_id}, "
                    "require_user_auth=true. Do not call another tool."
                )
                _, deny_chat, _ = await send_and_capture(
                    panel,
                    frame,
                    deny_prompt,
                    network,
                    keyboard_submit=True,
                )
                if deny_chat != chat_id:
                    raise AssertionError("side panel switched Chat before deny")
                deny_card = await pending_card(frame, "browser_click")
                await deny_card.locator('[data-action="interactive-cancel"]').click()
                await wait_turn_idle(frame, args.turn_timeout)
                if await target.evaluate("window.__skeinixClickCount") != 1:
                    raise AssertionError("denied UI action changed the page")
                denied_card = frame.locator(
                    '[data-role="interactive-artifact"]'
                ).filter(has_text=re.compile(r"(Denied|已拒绝)")).last
                await denied_card.get_by_text(
                    re.compile(r"^(Denied|已拒绝)$")
                ).wait_for(state="visible")
                if await denied_card.locator('[data-action="interactive-submit"]').count():
                    raise AssertionError("denied HITL remained interactive")
                print("[ok] UI deny returned not-executed and froze the card", flush=True)

                await stop_waiting_browser_turn(
                    panel=panel,
                    frame=frame,
                    api=api,
                    scope_id=scope_id,
                    chat_id=chat_id,
                    tab_id=tab_id,
                    target=target,
                    network=network,
                    console=console,
                    timeout_s=args.turn_timeout,
                )
                print("[ok] side-panel Stop cancelled only the current waiting Turn", flush=True)

                screenshot = Path(args.screenshot)
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                await panel.screenshot(path=str(screenshot), full_page=True)

                fatal = [
                    line
                    for line in console
                    if "pageerror:" in line
                    or "uncaught" in line.lower()
                    or "useLocation() may be used only" in line
                ]
                if fatal:
                    raise AssertionError(f"side-panel console errors: {fatal!r}")
    finally:
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
            # Closing the extension profile releases debugger control
            # asynchronously. Give that durable lifecycle event a moment to
            # reach the backend before deleting the short-lived test chats.
            await asyncio.sleep(1)
        for scope_id, chat_id in chats_to_delete:
            with contextlib.suppress(Exception):
                run = await active_run(api, scope_id, chat_id)
                if run is not None:
                    await api.post(
                        f"/api/v1/chats/{chat_id}/turns/{run['run_id']}/cancel"
                    )
                    await wait_no_active_run(api, scope_id, chat_id, timeout_s=30)
            for attempt in range(5):
                try:
                    await api.delete(
                        f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}?surface=browser"
                    )
                    break
                except Exception:
                    if attempt == 4:
                        break
                    await asyncio.sleep(0.5)
        await api.close()
        profile.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--fixture-port", type=int, default=9010)
    parser.add_argument("--turn-timeout", type=float, default=180)
    parser.add_argument("--stop-only", action="store_true")
    parser.add_argument(
        "--chromium",
        default=None,
        help="optional Chromium executable; defaults to Playwright's managed browser",
    )
    parser.add_argument(
        "--screenshot",
        default="/tmp/vibecanvas-sidepanel-resume-e2e.png",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
