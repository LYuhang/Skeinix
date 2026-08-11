#!/usr/bin/env python3
"""Scenario-driven real Chat -> Browser MCP -> MV3 extension acceptance.

The verifier sends natural browser tasks through the visible side-panel Chat.
Each task may use the prerequisite observe/act tools it needs. Playwright does
not perform the target actions: it independently watches the controlled page,
asserts the resulting URL/DOM/state, and captures evidence. This distinction is
important—a successful tool card alone is not browser acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from playwright.async_api import BrowserContext, Page, async_playwright

from verify_codex_browser_e2e import (
    Api,
    FixtureServer,
    _connect_extension,
    _extension_page,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS = [
    "browser_start_session",
    "browser_session_status",
    "browser_navigate",
    "browser_snapshot",
    "browser_query",
    "browser_read_text",
    "browser_get_attribute",
    "browser_get_html",
    "browser_take_screenshot",
    "browser_scroll",
    "browser_wait_for",
    "browser_tab",
    "browser_fetch_resource",
    "browser_click",
    "browser_type",
    "browser_select_option",
    "browser_press_key",
    "browser_check_login",
    "browser_end_session",
]


async def _put(api: Api, path: str, body: dict[str, Any]) -> dict[str, Any]:
    response = await api.client.put(path, headers=api.headers(), json=body)
    response.raise_for_status()
    return response.json()


def _copy_host_codex_identity(me: dict[str, Any]) -> Path:
    source = Path.home() / ".codex" / "auth.json"
    if not source.is_file():
        raise RuntimeError(f"host Codex identity is missing: {source}")
    runtime_root = Path(
        os.environ.get(
            "AGENT_RUNTIME_ROOT",
            str(Path.home() / ".vibecanvas" / "agent-runtime"),
        )
    ).resolve()
    account_root = (
        runtime_root
        / str(me["tenant_id"])
        / str(me["user_id"])
        / "codex-account-v1"
    ).resolve()
    if runtime_root not in account_root.parents:
        raise RuntimeError("refusing to copy Codex identity outside AGENT_RUNTIME_ROOT")
    account_home = account_root / ".codex"
    account_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    account_home.chmod(0o700)
    destination = account_home / "auth.json"
    shutil.copyfile(source, destination)
    destination.chmod(0o600)
    return account_root


async def _configure_runtime(api: Api, runtime: str) -> str | None:
    await _put(api, "/api/v1/agent-runtime/settings", {"default_runtime_type": runtime})
    if runtime == "codex":
        me = (await api.get("/api/v1/auth/me")).json()
        _copy_host_codex_identity(me)
    capabilities = (await api.get("/api/v1/agent-runtime/capabilities")).json()
    if not capabilities.get("runtime_available"):
        raise AssertionError(f"{runtime} Runtime unavailable: {capabilities!r}")
    if runtime == "codex":
        models = capabilities.get("models") or []
        account = next(
            (item for item in models if item.get("provider") == "chatgpt"),
            None,
        )
        if account is None:
            raise AssertionError("host identity exposed no Codex ChatGPT account model")
        return str(account["id"])
    value = capabilities.get("default_model_id")
    return str(value) if value else None


async def _stream_turn(
    api: Api,
    *,
    scope_id: str,
    chat_id: str,
    content: str,
    model_id: str | None,
) -> list[dict[str, Any]]:
    settings: dict[str, Any] = {}
    if model_id:
        settings["model_id"] = model_id
    body = {
        "role": "user",
        "content": content,
        "client_request_id": f"browser-all-tools-{uuid.uuid4().hex}",
        "mode": "browser",
        "approval_mode": "always_allow",
        "surface": "sidepanel",
        "agent_surface": "browser",
        "agent_settings": settings,
    }
    events: list[dict[str, Any]] = []
    url = f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
    async with api.client.stream(
        "POST", url, headers=api.headers(), json=body, timeout=None,
    ) as response:
        response.raise_for_status()
        event_name = ""
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                raw = line[5:].strip()
                try:
                    data: Any = json.loads(raw)
                except json.JSONDecodeError:
                    data = raw
                events.append({"event": event_name, "data": data})
    return events


def _call_name(call: dict[str, Any]) -> str:
    function = call.get("function")
    if isinstance(function, dict) and function.get("name"):
        return str(function["name"])
    return str(call.get("name") or call.get("tool_name") or "")


def _call_id(call: dict[str, Any]) -> str:
    return str(call.get("id") or call.get("tool_call_id") or "")


async def _history(api: Api, scope_id: str, chat_id: str) -> list[dict[str, Any]]:
    path = (
        f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
        "?limit=500&tail=true"
    )
    response = await api.client.get(path, headers=api.headers())
    if response.status_code == 404:
        # The UI assigns an id to an empty draft Chat before the first message
        # creates its durable row. Its correct pre-Turn history is empty.
        return []
    response.raise_for_status()
    payload = response.json()
    return list(payload.get("items") or [])


def _tool_counts(history: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for message in history:
        if message.get("role") != "assistant":
            continue
        for raw in message.get("tool_calls") or []:
            if not isinstance(raw, dict):
                continue
            name = _call_name(raw)
            if name:
                counts[name] = counts.get(name, 0) + 1
    return counts


def _assert_completed_calls(
    history: list[dict[str, Any]],
    *,
    before_counts: dict[str, int],
    required_increments: dict[str, int],
) -> None:
    results = {
        str(message.get("tool_call_id") or ""): message
        for message in history
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    seen_by_name: dict[str, int] = {}
    successful_by_name: dict[str, int] = {}
    pending: list[str] = []
    for message in history:
        if message.get("role") != "assistant":
            continue
        for raw in message.get("tool_calls") or []:
            if not isinstance(raw, dict):
                continue
            name = _call_name(raw)
            seen_by_name[name] = seen_by_name.get(name, 0) + 1
            if seen_by_name[name] <= before_counts.get(name, 0):
                continue
            if name not in required_increments:
                continue
            call_id = _call_id(raw)
            result = results.get(call_id)
            if result is None:
                pending.append(f"{name}:{call_id}")
                continue
            projection = json.dumps(result, ensure_ascii=False).lower()
            if not (
                '"status": "error"' in projection
                or '"status":"error"' in projection
            ):
                successful_by_name[name] = successful_by_name.get(name, 0) + 1
    if pending:
        raise AssertionError(f"new tool calls still pending durable results: {pending}")
    for name, increment in required_increments.items():
        if successful_by_name.get(name, 0) < increment:
            raise AssertionError(
                f"{name}: expected {increment} successful new call(s), got "
                f"{successful_by_name.get(name, 0)}"
            )


async def _adopt_browser_auth(api: Api, context: BrowserContext, base_url: str) -> bool:
    """Use the already signed-in visible browser without printing credentials."""
    # The visible app may have been opened as ``localhost`` while the verifier
    # addresses the same service as ``127.0.0.1``. Read this dedicated browser
    # profile's cookie jar and send only Skeinix's named Session/CSRF pair.
    cookies = {item["name"]: item["value"] for item in await context.cookies()}
    session_name = next((name for name in cookies if name.endswith("-web-session")), "")
    csrf_name = next((name for name in cookies if name.endswith("-web-csrf")), "")
    if not session_name or not csrf_name:
        return False
    api.cookie_header = (
        f"{session_name}={cookies[session_name]}; {csrf_name}={cookies[csrf_name]}"
    )
    api.csrf_token = cookies[csrf_name]
    response = await api.client.get("/api/v1/auth/me", headers=api.headers())
    if response.status_code >= 400:
        api.cookie_header = ""
        api.csrf_token = ""
        return False
    return True


def _tool_projection(
    history: list[dict[str, Any]], tool_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for message in history:
        if message.get("role") != "assistant":
            continue
        for raw in message.get("tool_calls") or []:
            if isinstance(raw, dict) and _call_name(raw) == tool_name:
                calls.append(raw)
    if not calls:
        raise AssertionError(f"no durable {tool_name} tool call in Chat history")
    call = calls[-1]
    call_id = _call_id(call)
    results = [
        message for message in history
        if message.get("role") == "tool"
        and str(message.get("tool_call_id") or "") == call_id
    ]
    if not results:
        raise AssertionError(f"no durable result for {tool_name} call {call_id}")
    result = results[-1]
    projection = json.dumps(result, ensure_ascii=False).lower()
    if '"status": "error"' in projection or '"status":"error"' in projection:
        raise AssertionError(f"{tool_name} returned an error: {projection[-3000:]}")
    return call, result


async def _invoke(
    api: Api,
    *,
    runtime: str,
    scope_id: str,
    chat_id: str,
    model_id: str | None,
    tool_name: str,
    instruction: str,
    expected_count: dict[str, int],
    timeout_s: float,
) -> dict[str, Any]:
    print(f"[{runtime}] invoking {tool_name}", flush=True)
    events = await asyncio.wait_for(
        _stream_turn(
            api,
            scope_id=scope_id,
            chat_id=chat_id,
            content=(
                f"/browser Call {tool_name} exactly once. {instruction} "
                "Do not call any other tool. After the tool returns, briefly report success."
            ),
            model_id=model_id,
        ),
        timeout=timeout_s,
    )
    history = await _history(api, scope_id, chat_id)
    call, result = _tool_projection(history, tool_name)
    count = sum(
        1
        for message in history
        if message.get("role") == "assistant"
        for item in message.get("tool_calls") or []
        if isinstance(item, dict) and _call_name(item) == tool_name
    )
    expected_count[tool_name] = expected_count.get(tool_name, 0) + 1
    if count != expected_count[tool_name]:
        raise AssertionError(
            f"{tool_name} durable count is {count}, expected {expected_count[tool_name]}"
        )
    if tool_name not in json.dumps(events, ensure_ascii=False):
        raise AssertionError(f"{tool_name} was durable but absent from the live SSE stream")
    return {"call": call, "result": result, "events": events}


async def _embed_frame(page: Page):
    deadline = asyncio.get_running_loop().time() + 45
    while asyncio.get_running_loop().time() < deadline:
        frame = next((item for item in page.frames if "/embed/chat" in item.url), None)
        if frame is not None and await frame.locator(
            '[data-role="agent-composer-input"]'
        ).count():
            return frame
        await asyncio.sleep(0.25)
    raise AssertionError(f"side-panel embed did not expose a composer: {[f.url for f in page.frames]!r}")


async def _capture_page(page: Page, path: Path) -> None:
    """Capture without Playwright's font-ready wait, which can hang on CDP Edge."""
    session = await page.context.new_cdp_session(page)
    try:
        payload = await session.send(
            "Page.captureScreenshot",
            {"format": "png", "fromSurface": True, "captureBeyondViewport": False},
        )
        path.write_bytes(base64.b64decode(payload["data"]))
    finally:
        await session.detach()


async def _invoke_sidepanel_scenario(
    api: Api,
    *,
    runtime: str,
    scope_id: str,
    chat_id: str,
    scenario_name: str,
    instruction: str,
    required_increments: dict[str, int],
    timeout_s: float,
    frame,
    target_page: Page,
) -> list[dict[str, Any]]:
    print(f"[{runtime}] scenario {scenario_name}: sending through side panel", flush=True)
    before = await _history(api, scope_id, chat_id)
    before_counts = _tool_counts(before)
    content = f"/browser {instruction}"
    composer = frame.locator('[data-role="agent-composer-input"]')
    # Emulate the user's short read/decision interval between Turns and avoid
    # racing the transient Query invalidation that follows durable completion.
    # The composer must remain idle across the settling window, not merely be
    # enabled for one animation frame.
    stable_deadline = asyncio.get_running_loop().time() + 5
    while True:
        if await composer.is_enabled() and not await frame.locator(
            '[data-action="agent-composer-stop"]'
        ).count():
            await asyncio.sleep(0.6)
            if await composer.is_enabled() and not await frame.locator(
                '[data-action="agent-composer-stop"]'
            ).count():
                break
        if asyncio.get_running_loop().time() >= stable_deadline:
            raise AssertionError(
                f"{scenario_name}: composer did not reach a stable idle state"
            )
        await asyncio.sleep(0.1)
    await composer.fill(content)
    send_button = frame.locator('[data-action="agent-composer-send"]')
    ready_deadline = asyncio.get_running_loop().time() + 5
    while not await send_button.is_enabled():
        if asyncio.get_running_loop().time() >= ready_deadline:
            raise AssertionError(
                f"{scenario_name}: Send did not become enabled after filling"
            )
        await asyncio.sleep(0.02)
    clicked_at = asyncio.get_running_loop().time()
    await send_button.click()
    clear_deadline = clicked_at + 0.25
    while await composer.input_value() != "" and asyncio.get_running_loop().time() < clear_deadline:
        await asyncio.sleep(0.01)
    if await composer.input_value() != "":
        print(
            f"[{runtime}] {scenario_name}: Send click was ignored; retrying through Ctrl+Enter",
            flush=True,
        )
        await composer.press("Control+Enter")
        fallback_deadline = asyncio.get_running_loop().time() + 0.25
        while (
            await composer.input_value() != ""
            and asyncio.get_running_loop().time() < fallback_deadline
        ):
            await asyncio.sleep(0.01)
        if await composer.input_value() != "":
            raise AssertionError(
                f"{scenario_name}: neither Send nor Ctrl+Enter cleared the composer within 250 ms"
            )
    clear_ms = int((asyncio.get_running_loop().time() - clicked_at) * 1000)
    bubble_deadline = asyncio.get_running_loop().time() + 1
    while (
        await frame.get_by_text(content, exact=True).count() != 1
        and asyncio.get_running_loop().time() < bubble_deadline
    ):
        await asyncio.sleep(0.02)
    if await frame.get_by_text(content, exact=True).count() != 1:
        raise AssertionError(
            f"{scenario_name}: optimistic user bubble is missing or duplicated"
        )
    print(f"[{runtime}] {scenario_name}: composer handoff={clear_ms}ms", flush=True)

    # Keep the controlled tab in front while the Agent works. The acceptance
    # operator can now see navigation, typing, and clicking instead of staring
    # only at tool cards in the side panel.
    await target_page.bring_to_front()
    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            history = await _history(api, scope_id, chat_id)
            _assert_completed_calls(
                history,
                before_counts=before_counts,
                required_increments=required_increments,
            )
            if not await composer.is_enabled():
                raise AssertionError(f"{scenario_name}: Turn is still active")
            await asyncio.sleep(0.5)
            if not await composer.is_enabled() or await frame.locator(
                '[data-action="agent-composer-stop"]'
            ).count():
                raise AssertionError(
                    f"{scenario_name}: composer did not remain idle after completion"
                )
            print(
                f"[{runtime}] scenario {scenario_name}: Agent tool chain complete",
                flush=True,
            )
            return history
        except Exception as exc:  # the live Turn may not be durable yet
            last_error = exc
            await asyncio.sleep(0.4)
    raise AssertionError(
        f"{scenario_name}: side-panel Turn did not complete: {last_error}"
    )


async def _install_fixture_behaviour(page: Page) -> None:
    await page.evaluate(
        """() => {
          document.body.style.minHeight = '2400px';
          const ready = document.createElement('div');
          ready.id = 'ready'; ready.textContent = 'Fixture ready';
          document.body.appendChild(ready);
          const spacer = document.createElement('div');
          spacer.style.height = '1800px';
          document.body.appendChild(spacer);
          const bottom = document.createElement('div');
          bottom.id = 'bottom'; bottom.textContent = 'Bottom marker';
          document.body.appendChild(bottom);
          window.__allToolsClickCount = 0;
          document.querySelector('#submit').addEventListener('click', () => {
            window.__allToolsClickCount += 1;
            let marker = document.querySelector('#clicked');
            if (!marker) {
              marker = document.createElement('div'); marker.id = 'clicked';
              document.body.appendChild(marker);
            }
            marker.textContent = `Clicked ${window.__allToolsClickCount}`;
          });
          document.querySelector('#reason').addEventListener('keydown', (event) => {
            if (event.key !== 'Enter') return;
            let marker = document.querySelector('#keyed');
            if (!marker) {
              marker = document.createElement('div'); marker.id = 'keyed';
              document.body.appendChild(marker);
            }
            marker.textContent = 'Enter observed';
          });
        }"""
    )


async def run(args: argparse.Namespace) -> None:
    runtime = args.runtime
    extension_dist = Path(args.extension_dir).expanduser().resolve()
    if not (extension_dist / "manifest.json").is_file():
        raise RuntimeError(f"extension build is missing: {extension_dist}")
    chromium = Path(args.chromium).expanduser().resolve() if args.chromium else None
    if chromium is not None and not chromium.is_file():
        raise RuntimeError(f"Chromium executable not found: {chromium}")

    api = Api(args.base_url, origin=args.origin)
    profile = tempfile.TemporaryDirectory(prefix=f"vibecanvas-browser-{runtime}-")
    context: BrowserContext | None = None
    scope_id = ""
    chat_id = f"browser-all-tools-{runtime}-{uuid.uuid4().hex}"
    fixture_base = f"http://127.0.0.1:{args.fixture_port}"
    initial_url = f"{fixture_base}/detail.html"
    target_url = f"{fixture_base}/page.html"
    expected_count: dict[str, int] = {}
    evidence_dir = Path(args.evidence_dir).expanduser().resolve() if args.evidence_dir else None
    if evidence_dir is not None:
        evidence_dir.mkdir(parents=True, exist_ok=True)
    try:
        if not args.cdp_endpoint:
            await api.login()

        with FixtureServer(args.fixture_port):
            async with async_playwright() as playwright:
                if args.cdp_endpoint:
                    browser = await playwright.chromium.connect_over_cdp(args.cdp_endpoint)
                    if not browser.contexts:
                        raise RuntimeError("the connected browser exposes no default context")
                    context = browser.contexts[0]
                    if args.sidepanel_ui:
                        # Prior interrupted acceptance runs may have left test
                        # side-panel shells and fixture tabs alive. Every shell
                        # mints its own scoped token and OPEN_WS deliberately
                        # replaces the prior socket, so duplicates make command
                        # effects occur while their observations are lost.
                        stale_prefixes = (
                            "chrome-extension://mkfldhmlgdbpmhplaphhcfcdcoaakcik/sidepanel.html",
                            fixture_base,
                            f"{args.base_url.rstrip('/')}/login",
                        )
                        for stale_page in list(context.pages):
                            if stale_page.url.startswith(stale_prefixes):
                                with contextlib.suppress(Exception):
                                    await stale_page.close()
                    target_page = await context.new_page()
                else:
                    context = await playwright.chromium.launch_persistent_context(
                        profile.name,
                        executable_path=(str(chromium) if chromium is not None else playwright.chromium.executable_path),
                        headless=not args.headed,
                        viewport={"width": 1280, "height": 900},
                        args=[
                            f"--disable-extensions-except={extension_dist}",
                            f"--load-extension={extension_dist}",
                            "--no-sandbox",
                        ],
                    )
                    target_page = context.pages[0]
                if args.cdp_endpoint and not await _adopt_browser_auth(
                    api, context, args.base_url
                ):
                    await api.login()
                model_id = await _configure_runtime(api, runtime)
                bootstrap = (
                    await api.get("/api/v1/chats/bootstrap?surface=browser")
                ).json()
                scope_id = str(bootstrap["carrier_scope_id"])
                if "browser" not in bootstrap.get("available_commands", []):
                    raise AssertionError("Browser surface did not advertise /browser")
                if args.sidepanel_ui:
                    app_page = await context.new_page()
                    await app_page.goto(
                        f"{args.base_url.rstrip('/')}/settings?tab=extensions",
                        wait_until="domcontentloaded",
                    )
                    email_input = app_page.locator("#login-email")
                    if await email_input.count():
                        email = os.environ.get("SKEINIX_ACCEPTANCE_EMAIL", "")
                        password = os.environ.get("SKEINIX_ACCEPTANCE_PASSWORD", "")
                        if not email or not password:
                            raise RuntimeError(
                                "side-panel UI login requires SKEINIX_ACCEPTANCE_EMAIL and "
                                "SKEINIX_ACCEPTANCE_PASSWORD"
                            )
                        await email_input.fill(email)
                        await app_page.locator('input[type="password"]').fill(password)
                        await app_page.locator('button[type="submit"]').click()
                        await app_page.wait_for_url(
                            lambda url: "/login" not in url,
                            timeout=45_000,
                        )
                    await app_page.wait_for_timeout(750)
                    await app_page.evaluate(
                        "document.dispatchEvent(new CustomEvent('skeinix:extension-auth-refresh'))"
                    )
                    await app_page.wait_for_timeout(750)
                await target_page.goto(initial_url)
                await target_page.wait_for_load_state("domcontentloaded")
                control_page = await _extension_page(context)
                if args.sidepanel_ui:
                    tab_id = int(
                        await control_page.evaluate(
                            """async (url) => {
                              const tabs = await chrome.tabs.query({});
                              const tab = tabs.find((item) => item.url === url);
                              if (!tab || typeof tab.id !== 'number') {
                                throw new Error('acceptance target tab not found');
                              }
                              return tab.id;
                            }""",
                            initial_url,
                        )
                    )
                    print(
                        f"[{runtime}] real side-panel connection will control tab {tab_id}",
                        flush=True,
                    )
                else:
                    tab_id = await _connect_extension(
                        api, control_page, initial_url, args.ws_base,
                    )
                    print(
                        f"[{runtime}] extension connected to real tab {tab_id}",
                        flush=True,
                    )
                sidepanel_frame = None
                if args.sidepanel_ui:
                    # A prior interrupted manual run may leave chrome.debugger
                    # attached even after its temporary Chat is deleted. Reset
                    # that device-local session before creating the acceptance
                    # Chat; this is setup cleanup, not one of the Agent tool
                    # assertions below.
                    await control_page.evaluate(
                        """async () => {
                          const state = await chrome.storage.session.get([
                            'controlledTabIds', 'currentBrowserSession'
                          ]);
                          if (!Array.isArray(state.controlledTabIds) || state.controlledTabIds.length === 0) return;
                          await chrome.runtime.sendMessage({
                            type: 'RUN_COMMAND',
                            env: {
                              v: 1,
                              kind: 'command',
                              id: `acceptance-reset-${Date.now()}`,
                              channel: `chat:${state.currentBrowserSession?.chatId || 'acceptance-reset'}`,
                              transport: 'acceptance-reset',
                              producer: 'acceptance-reset',
                              data: { cmd: 'end_session', args: { reason: 'acceptance_reset' } }
                            }
                          });
                        }"""
                    )
                    await control_page.wait_for_timeout(500)
                    await control_page.set_viewport_size({"width": 430, "height": 900})
                    sidepanel_frame = await _embed_frame(control_page)
                    chat_tab = sidepanel_frame.locator('[data-action="embed-tab-chat"]')
                    if await chat_tab.count():
                        await chat_tab.click()
                    composer = sidepanel_frame.locator('[data-role="agent-composer-input"]')
                    previous_chat_id = await composer.get_attribute("data-chat-id")
                    new_chat = sidepanel_frame.locator('[data-action="agent-sidebar-new-chat"]')
                    if await new_chat.count():
                        await new_chat.click()
                        if previous_chat_id:
                            deadline = asyncio.get_running_loop().time() + 20
                            while asyncio.get_running_loop().time() < deadline:
                                if await composer.get_attribute("data-chat-id") != previous_chat_id:
                                    break
                                await asyncio.sleep(0.2)
                    chat_id = str(await composer.get_attribute("data-chat-id") or "")
                    if not chat_id:
                        raise AssertionError("side-panel New Chat did not expose a Chat id")
                    await control_page.bring_to_front()
                    print(f"[{runtime}] side-panel Chat ready: {chat_id}", flush=True)

                if sidepanel_frame is not None:
                    search_url = f"{fixture_base}/search.html"
                    search_term = "Skeinix browser automation"

                    async def scenario(
                        name: str,
                        instruction: str,
                        required: dict[str, int],
                    ) -> list[dict[str, Any]]:
                        await control_page.bring_to_front()
                        history = await _invoke_sidepanel_scenario(
                            api,
                            runtime=runtime,
                            scope_id=scope_id,
                            chat_id=chat_id,
                            scenario_name=name,
                            instruction=instruction,
                            required_increments=required,
                            timeout_s=args.turn_timeout,
                            frame=sidepanel_frame,
                            target_page=target_page,
                        )
                        if evidence_dir is not None:
                            await _capture_page(
                                target_page, evidence_dir / f"{name}-target.png"
                            )
                            await control_page.bring_to_front()
                            await _capture_page(
                                control_page, evidence_dir / f"{name}-sidepanel.png"
                            )
                            await target_page.bring_to_front()
                        return history

                    await scenario(
                        "01-connect-and-observe",
                        (
                            f"Take control of the existing browser tab {tab_id} and visibly open "
                            f"{search_url}. Use browser_start_session with target='existing', "
                            f"tab={tab_id}, require_user_auth=false; then inspect the live session "
                            "with browser_session_status. Navigate to the URL with browser_navigate, "
                            "take a pruned browser_snapshot of body, and capture a viewport "
                            "browser_take_screenshot. Keep the controlled page open and report the "
                            "final title and URL. Do not release the browser session."
                        ),
                        {
                            "browser_start_session": 1,
                            "browser_session_status": 1,
                            "browser_navigate": 1,
                            "browser_snapshot": 1,
                            "browser_take_screenshot": 1,
                        },
                    )
                    await target_page.wait_for_url(search_url, timeout=15_000)
                    if await target_page.title() != "Skeinix Browser Search":
                        raise AssertionError("connect scenario opened the wrong page title")
                    await target_page.locator("#search-input").wait_for(state="visible")
                    print(
                        f"[{runtime}] 01-connect-and-observe: target URL/title/DOM verified",
                        flush=True,
                    )

                    await scenario(
                        "02-search-and-open-result",
                        (
                            f"On the controlled search page, complete a real search for "
                            f"{search_term!r}. Follow this observe-act-observe chain: use "
                            "browser_query to locate #search-input; use its returned handle with "
                            "browser_type (replace=true); submit it with browser_press_key key='Enter' "
                            "and expect='#search-results'; use browser_wait_for for #search-results; "
                            "take a fresh browser_snapshot; query #primary-result; read its visible "
                            "label with browser_read_text and inspect its href with "
                            "browser_get_attribute; click that result with browser_click and "
                            "expect='#detail-title'; wait for #detail-title; read #acceptance-trail "
                            "with browser_read_text; inspect #detail-content with browser_get_html "
                            "format='markdown'; and finish with browser_take_screenshot. Use fresh "
                            "handles after each navigation. Do not merely describe the operations—"
                            "perform every one and leave the detail page visible."
                        ),
                        {
                            "browser_query": 2,
                            "browser_type": 1,
                            "browser_press_key": 1,
                            "browser_wait_for": 2,
                            "browser_snapshot": 1,
                            "browser_read_text": 2,
                            "browser_get_attribute": 1,
                            "browser_click": 1,
                            "browser_get_html": 1,
                            "browser_take_screenshot": 1,
                        },
                    )
                    await target_page.wait_for_url(
                        lambda value: "/detail.html?from=" in value,
                        timeout=15_000,
                    )
                    trail = " ".join(
                        (await target_page.locator("#acceptance-trail").inner_text()).split()
                    )
                    if search_term not in trail or "Results page observed: yes" not in trail:
                        raise AssertionError(f"search journey did not reach results: {trail!r}")
                    if "Result clicked: yes" not in trail:
                        raise AssertionError(f"search result was not actually clicked: {trail!r}")
                    print(
                        f"[{runtime}] 02-search-and-open-result: query, results, click, and detail verified",
                        flush=True,
                    )

                    marker = "SKEINIX_FORM_OK"
                    resource_path = f"/data/browser-acceptance/{runtime}-pixel.png"
                    await scenario(
                        "03-form-resource-and-scroll",
                        (
                            f"Now navigate the same controlled tab to {target_url}. Snapshot the "
                            "page, then use one browser_query for "
                            "'#reason,#decision,#submit,#thumb,#bottom'. Using the returned handles, "
                            f"type {marker!r} into #reason with replace=true, select 'reject' in "
                            "#decision, click #submit with expect='#clicked', press Enter on #reason "
                            "with expect='#keyed', and wait for both markers. Fetch #thumb as an "
                            f"image with browser_fetch_resource to {resource_path!r} (max 100000 "
                            "bytes), scroll #bottom into view with browser_scroll, take a screenshot, "
                            "and use browser_check_login. Re-observe after mutations as needed. Leave "
                            "the form page visible and do not release control."
                        ),
                        {
                            "browser_navigate": 1,
                            "browser_snapshot": 1,
                            "browser_query": 1,
                            "browser_type": 1,
                            "browser_select_option": 1,
                            "browser_click": 1,
                            "browser_press_key": 1,
                            "browser_wait_for": 1,
                            "browser_fetch_resource": 1,
                            "browser_scroll": 1,
                            "browser_take_screenshot": 1,
                            "browser_check_login": 1,
                        },
                    )
                    await target_page.wait_for_url(target_url, timeout=15_000)
                    if await target_page.locator("#reason").input_value() != marker:
                        raise AssertionError("browser_type did not update the real form")
                    if await target_page.locator("#decision").input_value() != "reject":
                        raise AssertionError("browser_select_option did not update the real form")
                    if await target_page.locator("#clicked").inner_text() != "Clicked 1":
                        raise AssertionError("browser_click did not click exactly once")
                    if await target_page.locator("#keyed").inner_text() != "Enter observed":
                        raise AssertionError("browser_press_key did not reach the page handler")
                    if await target_page.evaluate("window.scrollY") <= 0:
                        raise AssertionError("browser_scroll did not move the real page")
                    workspace = (
                        await api.get(f"/api/v1/chats/workspace?chat_id={chat_id}")
                    ).json()
                    fetched = await api.get(
                        "/api/v1/vfs/content",
                        params={
                            "wf_id": workspace["workspace_scope_id"],
                            "path": resource_path,
                        },
                    )
                    if fetched.json().get("size_bytes", 0) <= 0:
                        raise AssertionError(
                            "browser_fetch_resource created no durable VFS bytes"
                        )
                    print(
                        f"[{runtime}] 03-form-resource-and-scroll: live form, VFS bytes, and scroll verified",
                        flush=True,
                    )

                    await scenario(
                        "04-tabs-and-release",
                        (
                            "Exercise the real multi-tab lifecycle. Query #open-detail and click it "
                            "to open the target=_blank detail page. Then call browser_tab with "
                            "action='wait_new', list the controlled tabs with action='list', use the "
                            "returned detail tab with action='use', bring it forward with "
                            "action='switch', snapshot and read #detail-title on that tab, close only "
                            "the detail excursion with browser_tab action='close', list tabs again, "
                            "and finally call browser_end_session with require_user_auth=false. "
                            "Do not close the root form tab."
                        ),
                        {
                            "browser_query": 1,
                            "browser_click": 1,
                            "browser_tab": 6,
                            "browser_snapshot": 1,
                            "browser_read_text": 1,
                            "browser_end_session": 1,
                        },
                    )
                    released = (
                        await api.get(f"/api/v1/chats/{chat_id}/browser-binding")
                    ).json()
                    if released.get("status") != "inactive":
                        raise AssertionError(
                            f"browser session was not durably released: {released!r}"
                        )
                    history = await _history(api, scope_id, chat_id)
                    durable_counts = _tool_counts(history)
                    missing = [name for name in TOOLS if durable_counts.get(name, 0) < 1]
                    if missing:
                        raise AssertionError(f"public browser tools not covered: {missing}")
                    print(
                        f"browser_scenario_acceptance_{runtime}=pass ({len(TOOLS)}/19)",
                        flush=True,
                    )
                    return

                async def invoke(tool: str, instruction: str) -> dict[str, Any]:
                    # The visible side-panel scenario returns above after its
                    # natural multi-tool journeys. This fallback is the direct
                    # API, one-tool-at-a-time verifier only.
                    result = await _invoke(
                        api,
                        runtime=runtime,
                        scope_id=scope_id,
                        chat_id=chat_id,
                        model_id=model_id,
                        tool_name=tool,
                        instruction=instruction,
                        expected_count=expected_count,
                        timeout_s=args.turn_timeout,
                    )
                    if evidence_dir is not None:
                        index = TOOLS.index(tool) + 1
                        await _capture_page(
                            target_page,
                            evidence_dir / f"{index:02d}-{tool}-target.png",
                        )
                        if sidepanel_frame is not None:
                            await _capture_page(
                                control_page,
                                evidence_dir / f"{index:02d}-{tool}-sidepanel.png",
                            )
                    return result

                await invoke(
                    "browser_start_session",
                    f"Use target='existing', tab={tab_id}, require_user_auth=false.",
                )
                await invoke(
                    "browser_session_status",
                    "Use require_user_auth=false and inspect the active controlled tab.",
                )
                await invoke(
                    "browser_navigate",
                    f"Navigate tab={tab_id} to {target_url!r} with wait_until='load', "
                    "timeout_ms=15000, require_user_auth=false.",
                )
                await target_page.wait_for_url(target_url)

                await invoke(
                    "browser_snapshot",
                    f"Snapshot tab={tab_id}, scope='body', prune=true.",
                )
                await invoke(
                    "browser_query",
                    f"Query tab={tab_id} with selector='#reason,#decision,#thumb'.",
                )
                reason_handle = await target_page.locator("#reason").get_attribute(
                    "data-skeinix-h"
                )
                decision_handle = await target_page.locator("#decision").get_attribute(
                    "data-skeinix-h"
                )
                thumb_handle = await target_page.locator("#thumb").get_attribute(
                    "data-skeinix-h"
                )
                if not all((reason_handle, decision_handle, thumb_handle)):
                    raise AssertionError("browser_query did not stamp all expected element handles")

                await invoke(
                    "browser_read_text",
                    f"Read tab={tab_id}, selector='#title', max_chars=1000.",
                )
                await invoke(
                    "browser_get_attribute",
                    f"Read name='src' from handle={thumb_handle!r}, tab={tab_id}.",
                )
                await invoke(
                    "browser_get_html",
                    f"Read selector='body' from tab={tab_id}, format='html', max_chars=8000.",
                )
                await invoke(
                    "browser_take_screenshot",
                    f"Capture tab={tab_id} with full_page=true.",
                )
                await invoke(
                    "browser_scroll",
                    f"Scroll tab={tab_id} without a handle.",
                )
                if await target_page.evaluate("window.scrollY") <= 0:
                    raise AssertionError("browser_scroll did not move the real page")
                await invoke(
                    "browser_wait_for",
                    f"Wait for selector='#ready' on tab={tab_id} with timeout_ms=8000.",
                )
                await invoke(
                    "browser_tab",
                    "Use action='list' and require_user_auth=false.",
                )

                resource_path = f"/data/browser-acceptance/{runtime}-pixel.png"
                await invoke(
                    "browser_fetch_resource",
                    f"Fetch selector='#thumb' from tab={tab_id}, type='image', "
                    f"save_path={resource_path!r}, max_bytes=100000, "
                    "require_user_auth=false.",
                )
                workspace = (
                    await api.get(f"/api/v1/chats/workspace?chat_id={chat_id}")
                ).json()
                fetched = await api.get(
                    "/api/v1/vfs/content",
                    params={
                        "wf_id": workspace["workspace_scope_id"],
                        "path": resource_path,
                    },
                )
                if fetched.json().get("size_bytes", 0) <= 0:
                    raise AssertionError("browser_fetch_resource created no durable VFS bytes")

                await invoke(
                    "browser_click",
                    f"Click selector='#submit' on tab={tab_id}, expect='#clicked', "
                    "purpose='acceptance click', require_user_auth=false.",
                )
                if await target_page.evaluate("window.__allToolsClickCount") != 1:
                    raise AssertionError("browser_click did not click the real page exactly once")
                marker = f"typed-{runtime}-{uuid.uuid4().hex[:8]}"
                await invoke(
                    "browser_type",
                    f"Type text={marker!r} into handle={reason_handle!r}, tab={tab_id}, "
                    "replace=true, purpose='acceptance typing', require_user_auth=false.",
                )
                if await target_page.locator("#reason").input_value() != marker:
                    raise AssertionError("browser_type did not set the real input value")
                await invoke(
                    "browser_select_option",
                    f"Select option='reject' in handle={decision_handle!r}, tab={tab_id}, "
                    "purpose='acceptance selection', require_user_auth=false.",
                )
                if await target_page.locator("#decision").input_value() != "reject":
                    raise AssertionError("browser_select_option did not update the real select")
                await invoke(
                    "browser_press_key",
                    f"Press key='Enter' on handle={reason_handle!r}, tab={tab_id}, "
                    "expect='#keyed', purpose='acceptance key', require_user_auth=false.",
                )
                await target_page.locator("#keyed").wait_for(state="visible")
                await invoke(
                    "browser_check_login",
                    f"Check login on tab={tab_id}.",
                )
                await invoke(
                    "browser_end_session",
                    "Use reason='all_tools_acceptance_complete', require_user_auth=false.",
                )

                released = (
                    await api.get(f"/api/v1/chats/{chat_id}/browser-binding")
                ).json()
                if released.get("status") != "inactive":
                    raise AssertionError(f"browser session was not durably released: {released!r}")

                history = await _history(api, scope_id, chat_id)
                durable_names = [
                    _call_name(call)
                    for message in history
                    if message.get("role") == "assistant"
                    for call in message.get("tool_calls") or []
                    if isinstance(call, dict)
                ]
                for tool in TOOLS:
                    if durable_names.count(tool) != 1:
                        raise AssertionError(
                            f"{tool} durable count is {durable_names.count(tool)}, expected 1"
                        )
                print(f"browser_all_tools_{runtime}=pass ({len(TOOLS)}/19)", flush=True)
    finally:
        if context is not None and not args.cdp_endpoint:
            with contextlib.suppress(Exception):
                await context.close()
        if scope_id:
            with contextlib.suppress(Exception):
                await api.delete(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}?surface=browser"
                )
        await api.close()
        profile.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=("langchain", "codex"), required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--ws-base", default="ws://127.0.0.1:9001")
    parser.add_argument("--origin", default="http://127.0.0.1:9001")
    parser.add_argument("--fixture-port", type=int, default=9010)
    parser.add_argument("--turn-timeout", type=float, default=360)
    parser.add_argument(
        "--chromium",
        default="",
        help="optional Chromium executable; defaults to Playwright's managed browser",
    )
    parser.add_argument(
        "--extension-dir",
        default=str(REPO_ROOT / "extension" / "dist"),
        help="unpacked extension directory to load (for example a downloaded ZIP extraction)",
    )
    parser.add_argument("--headed", action="store_true", help="show the real Chromium window")
    parser.add_argument(
        "--cdp-endpoint",
        default="",
        help="attach to an already visible Chromium/Edge instance instead of launching one",
    )
    parser.add_argument(
        "--sidepanel-ui",
        action="store_true",
        help="send every acceptance instruction through the real side-panel composer",
    )
    parser.add_argument("--evidence-dir", default="", help="optional per-tool screenshot directory")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
