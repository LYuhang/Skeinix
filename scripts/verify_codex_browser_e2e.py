#!/usr/bin/env python3
"""Real Codex -> Browser MCP -> extension end-to-end smoke test.

This verifier intentionally uses:

* the public Chat/SSE and HITL HTTP contracts;
* a real Codex app-server Runtime turn;
* the built MV3 extension in a real Chromium persistent context;
* the extension's offscreen WebSocket and chrome.debugger command path.

It does not call Browser tool implementations directly.  The dev-only browser
debug endpoint is used only as a readiness probe and for deterministic cleanup.

``--transport-only`` stops after a real Cookie/Bearer -> scoped-token -> clean
WebSocket URL -> MV3 offscreen/service-worker -> ``chrome.debugger`` roundtrip.
It is the credential-transport gate and deliberately does not require a model;
the default mode retains the complete Codex, HITL, approve/deny/cancel flow.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import tempfile
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import BrowserContext, Page, async_playwright


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ID = "mkfldhmlgdbpmhplaphhcfcdcoaakcik"
class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


class FixtureServer:
    def __init__(self, port: int) -> None:
        fixture_root = REPO_ROOT / "extension" / "test-fixtures"

        def handler(*args: object, **kwargs: object) -> _QuietHandler:
            return _QuietHandler(*args, directory=str(fixture_root), **kwargs)

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
    def __init__(self, base_url: str, *, origin: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=90)
        self.token = ""
        self.origin = origin.rstrip("/")
        self.cookie_header = ""
        self.csrf_token = ""

    async def close(self) -> None:
        await self.client.aclose()

    def headers(self) -> dict[str, str]:
        headers = {"origin": self.origin}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        elif self.cookie_header:
            # Development verification commonly talks to the HTTP API directly,
            # while the production-shape cookies deliberately carry ``Secure``.
            # Send the exact browser cookie values explicitly so HTTPX does not
            # suppress them solely because the local hop is plaintext.
            headers["cookie"] = self.cookie_header
            headers["x-csrf-token"] = self.csrf_token
        return headers

    async def login(self) -> None:
        email = os.environ.get("SKEINIX_ACCEPTANCE_EMAIL", "test")
        password = os.environ.get("SKEINIX_ACCEPTANCE_PASSWORD", "test")
        response = await self.client.post(
            "/api/v1/auth/login",
            headers={"origin": self.origin},
            json={"email": email, "password": password},
        )
        response.raise_for_status()
        payload = response.json()
        self.token = str(payload.get("session_token") or "")
        if self.token:
            return
        cookies = {cookie.name: cookie.value for cookie in response.cookies.jar}
        try:
            session_name = next(name for name in cookies if name.endswith("-web-session"))
            csrf_name = next(name for name in cookies if name.endswith("-web-csrf"))
        except StopIteration as exc:
            raise AssertionError(
                "login returned neither a bearer token nor the web Session cookies"
            ) from exc
        self.cookie_header = (
            f"{session_name}={cookies[session_name]}; {csrf_name}={cookies[csrf_name]}"
        )
        self.csrf_token = cookies[csrf_name]

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        response = await self.client.get(path, headers=self.headers(), **kwargs)
        response.raise_for_status()
        return response

    async def post(self, path: str, body: dict[str, Any]) -> httpx.Response:
        response = await self.client.post(path, headers=self.headers(), json=body)
        response.raise_for_status()
        return response

    async def delete(self, path: str) -> httpx.Response:
        response = await self.client.delete(path, headers=self.headers())
        response.raise_for_status()
        return response


async def _extension_page(context: BrowserContext) -> Page:
    page = await context.new_page()
    await page.goto(f"chrome-extension://{EXTENSION_ID}/sidepanel.html")
    await page.wait_for_load_state("domcontentloaded")
    return page


async def _connect_extension(
    api: Api,
    control_page: Page,
    fixture_url: str,
    ws_base: str,
) -> int:
    target = await control_page.evaluate(
        """async (url) => {
          const tabs = await chrome.tabs.query({});
          const tab = tabs.find((item) => item.url === url);
          if (!tab || typeof tab.id !== "number") throw new Error("fixture tab not found");
          await chrome.runtime.sendMessage({
            type: "SIDEPANEL_WINDOW",
            windowId: tab.windowId,
            panelContextId: "codex-browser-e2e",
          });
          return {tabId: tab.id, windowId: tab.windowId};
        }""",
        fixture_url,
    )
    scoped = (
        await api.post(
            "/api/v1/browser/token",
            {
                "wf_id": "codex-browser-e2e",
                "browser_id": "codex-browser-e2e",
            },
        )
    ).json()["token"]
    opened = await control_page.evaluate(
        """async ({token, wsBase}) => chrome.runtime.sendMessage({
          type: "OPEN_WS",
          scopedToken: token,
          wsBase,
          browser: "codex-browser-e2e",
        })""",
        {"token": scoped, "wsBase": ws_base},
    )
    if not opened or not opened.get("ok"):
        raise AssertionError(f"extension WebSocket open failed: {opened!r}")

    deadline = time.monotonic() + 15
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = (
                await api.post(
                    "/api/v1/browser/debug/send",
                    {"cmd": "list_open_tabs", "args": {}},
                )
            ).json()
            if last.get("observation", {}).get("ok"):
                return int(target["tabId"])
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                # Production-shape deployments intentionally disable the
                # dev-only debug command endpoint. The first real
                # browser_start_session call remains the authoritative
                # transport readiness gate in that configuration.
                await asyncio.sleep(0.75)
                return int(target["tabId"])
            if exc.response.status_code != 409:
                raise
        await asyncio.sleep(0.25)
    raise AssertionError(f"extension did not become ready: {last!r}")


async def _stream_turn(
    api: Api,
    *,
    scope_id: str,
    chat_id: str,
    content: str,
    approval_mode: str,
    turn_id_ready: asyncio.Future[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    body = {
        "role": "user",
        "content": content,
        "client_request_id": f"e2e-{uuid.uuid4().hex}",
        "mode": "browser",
        "approval_mode": approval_mode,
        "surface": "sidepanel",
        "agent_surface": "browser",
        "agent_settings": {
            "model_id": "gpt-5.6-luna",
            "reasoning_effort": "low",
        },
    }
    events: list[dict[str, Any]] = []
    url = f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
    async with api.client.stream(
        "POST",
        url,
        headers=api.headers(),
        json=body,
        timeout=None,
    ) as response:
        response.raise_for_status()
        turn_id = response.headers["x-turn-id"]
        if turn_id_ready is not None and not turn_id_ready.done():
            turn_id_ready.set_result(turn_id)
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
    return turn_id, events


async def _pending_hitl(
    api: Api,
    *,
    scope_id: str,
    turn_id: str,
    timeout_s: float = 90,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        last = (
            await api.get(f"/api/v1/chat-scopes/{scope_id}/active-runs")
        ).json()
        for run in last:
            if run.get("run_id") == turn_id and run.get("pending_hitl"):
                return run["pending_hitl"][0]
        await asyncio.sleep(0.25)
    raise AssertionError(f"no durable pending HITL for turn {turn_id}: {last!r}")


def _event_text(events: list[dict[str, Any]]) -> str:
    return json.dumps(events, ensure_ascii=False)


def _browser_error_code(observation: dict[str, Any]) -> str:
    """Read the stable extension error code from the observation envelope."""
    data = observation.get("data")
    if isinstance(data, dict) and isinstance(data.get("error_code"), str):
        return data["error_code"]
    return str(observation.get("error_code") or "")


async def run(args: argparse.Namespace) -> None:
    extension_dist = REPO_ROOT / "extension" / "dist"
    if not (extension_dist / "manifest.json").exists():
        raise RuntimeError("extension/dist is missing; build the extension first")
    chromium = Path(args.chromium) if args.chromium else None
    if chromium is not None and not chromium.exists():
        raise RuntimeError(f"Chromium executable not found: {chromium}")

    api = Api(args.base_url, origin=args.origin)
    chat_id = f"browser-e2e-{uuid.uuid4().hex}"
    scope_id = ""
    profile = tempfile.TemporaryDirectory(prefix="vibecanvas-browser-e2e-")
    context: BrowserContext | None = None
    fixture_url = f"http://127.0.0.1:{args.fixture_port}/page.html"
    try:
        await api.login()
        if not args.transport_only:
            settings = (await api.get("/api/v1/agent-runtime/settings")).json()
            if settings["default_runtime_type"] != "codex":
                raise AssertionError("test user default Runtime must be codex")
            capabilities = (await api.get("/api/v1/agent-runtime/capabilities")).json()
            if not capabilities.get("runtime_available") or capabilities.get("error_code"):
                raise AssertionError(f"Codex broker Runtime is unavailable: {capabilities!r}")
            scope_id = (
                await api.get("/api/v1/chats/bootstrap?surface=browser")
            ).json()["carrier_scope_id"]

        with FixtureServer(args.fixture_port):
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    profile.name,
                    executable_path=str(chromium) if chromium else None,
                    headless=True,
                    args=[
                        f"--disable-extensions-except={extension_dist}",
                        f"--load-extension={extension_dist}",
                        "--no-sandbox",
                    ],
                )
                target_page = context.pages[0]
                await target_page.goto(fixture_url)
                await target_page.wait_for_load_state("domcontentloaded")
                await target_page.evaluate(
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
                control_page = await _extension_page(context)
                tab_id = await _connect_extension(
                    api, control_page, fixture_url, args.ws_base
                )
                print(f"[ok] extension connected; fixture tab={tab_id}")

                if args.transport_only:
                    started = (
                        await api.post(
                            "/api/v1/browser/debug/send",
                            {
                                "cmd": "start_session",
                                "args": {
                                    "target": "existing",
                                    "tab": tab_id,
                                    "require_user_auth": True,
                                },
                            },
                        )
                    ).json()
                    if not started.get("observation", {}).get("ok"):
                        raise AssertionError(f"browser start failed: {started!r}")
                    read = (
                        await api.post(
                            "/api/v1/browser/debug/send",
                            {
                                "cmd": "read_text",
                                "args": {"tab": tab_id, "selector": "#title"},
                            },
                        )
                    ).json()
                    observation = read.get("observation", {})
                    if not observation.get("ok") or "Review Item 42" not in str(
                        observation
                    ):
                        raise AssertionError(f"browser read failed: {read!r}")
                    ended = (
                        await api.post(
                            "/api/v1/browser/debug/send",
                            {
                                "cmd": "end_session",
                                "args": {"reason": "transport_e2e_complete"},
                            },
                        )
                    ).json()
                    if not ended.get("observation", {}).get("ok"):
                        raise AssertionError(f"browser release failed: {ended!r}")
                    print("[ok] real chrome.debugger start/read/end roundtrip")
                    print("browser_extension_transport_e2e=pass")
                    return

                # A prior verifier may intentionally have stopped only its
                # Agent Turn, leaving browser control bound to that Chat. The
                # product must preserve that distinction; this isolated smoke
                # test explicitly releases the stale fixture session before it
                # creates a different Chat.
                cleanup = (
                    await api.post(
                        "/api/v1/browser/debug/send",
                        {"cmd": "end_session", "args": {"reason": "e2e_setup"}},
                    )
                ).json()
                if not cleanup.get("observation", {}).get("ok"):
                    raise AssertionError(f"pre-test browser release failed: {cleanup!r}")
                await asyncio.sleep(0.5)

                setup_prompt = (
                    f"/browser Call browser_start_session with target='existing', "
                    f"tab={tab_id}, require_user_auth=true. Then call "
                    f"browser_read_text with selector='#title', tab={tab_id}. "
                    "Do not modify the page. Reply with the exact title text."
                )
                _, setup_events = await asyncio.wait_for(
                    _stream_turn(
                        api,
                        scope_id=scope_id,
                        chat_id=chat_id,
                        content=setup_prompt,
                        approval_mode="always_allow",
                    ),
                    timeout=args.turn_timeout,
                )
                setup_text = _event_text(setup_events)
                if "Review Item 42" not in setup_text:
                    raise AssertionError(
                        "Codex Browser read turn did not return fixture title; "
                        f"events={setup_text[-4000:]}"
                    )
                print("[ok] Codex started the exact existing tab and read its text")

                turn_ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                approve_task = asyncio.create_task(
                    _stream_turn(
                        api,
                        scope_id=scope_id,
                        chat_id=chat_id,
                        content=(
                            f"Use browser_click exactly once with selector='#submit', "
                            f"tab={tab_id}, require_user_auth=true, and explain that "
                            "this submits the fixture. Do not call another mutating tool."
                        ),
                        approval_mode="agent",
                        turn_id_ready=turn_ready,
                    )
                )
                approve_turn = await asyncio.wait_for(turn_ready, timeout=10)
                pending = await _pending_hitl(
                    api, scope_id=scope_id, turn_id=approve_turn
                )
                if pending["hitl_type"] != "pre_tool_approval":
                    raise AssertionError(f"unexpected HITL type: {pending!r}")
                if await target_page.evaluate("window.__skeinixClickCount") != 0:
                    raise AssertionError("browser click executed before approval")
                print("[ok] browser_click suspended before execution and HITL is durable")
                decision = (
                    await api.post(
                        f"/api/v1/hitl-requests/{pending['hitl_request_id']}/decision",
                        {"decision": "approve"},
                    )
                ).json()
                if not decision.get("decision_applied"):
                    raise AssertionError(f"approval was not applied: {decision!r}")
                _, approve_events = await asyncio.wait_for(
                    approve_task, timeout=args.turn_timeout
                )
                click_count = await target_page.evaluate("window.__skeinixClickCount")
                if click_count != 1:
                    raise AssertionError(f"approved click count is {click_count}, expected 1")
                if "browser_click" not in _event_text(approve_events):
                    raise AssertionError("approved turn did not expose browser_click events")
                print("[ok] approved tool resumed once and changed the real page once")

                deny_ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                deny_task = asyncio.create_task(
                    _stream_turn(
                        api,
                        scope_id=scope_id,
                        chat_id=chat_id,
                        content=(
                            f"Use browser_click exactly once with selector='#submit', "
                            f"tab={tab_id}, require_user_auth=true. Do not use another tool."
                        ),
                        approval_mode="always_ask",
                        turn_id_ready=deny_ready,
                    )
                )
                deny_turn = await asyncio.wait_for(deny_ready, timeout=10)
                pending = await _pending_hitl(api, scope_id=scope_id, turn_id=deny_turn)
                await api.post(
                    f"/api/v1/hitl-requests/{pending['hitl_request_id']}/decision",
                    {"decision": "deny"},
                )
                _, deny_events = await asyncio.wait_for(
                    deny_task, timeout=args.turn_timeout
                )
                if await target_page.evaluate("window.__skeinixClickCount") != 1:
                    raise AssertionError("denied browser click changed the page")
                deny_text = _event_text(deny_events)
                if "not_executed" not in deny_text and "未授权" not in deny_text:
                    raise AssertionError(
                        f"denial result was not visible to the Agent: {deny_text[-4000:]}"
                    )
                print("[ok] denied tool was not executed and denial reached the Agent")

                cancel_ready: asyncio.Future[str] = asyncio.get_running_loop().create_future()
                cancel_task = asyncio.create_task(
                    _stream_turn(
                        api,
                        scope_id=scope_id,
                        chat_id=chat_id,
                        content=(
                            f"Use browser_click with selector='#submit', tab={tab_id}, "
                            "require_user_auth=true. Do not use another tool."
                        ),
                        approval_mode="always_ask",
                        turn_id_ready=cancel_ready,
                    )
                )
                cancel_turn = await asyncio.wait_for(cancel_ready, timeout=10)
                await _pending_hitl(api, scope_id=scope_id, turn_id=cancel_turn)
                await api.post(
                    f"/api/v1/chats/{chat_id}/turns/{cancel_turn}/cancel", {}
                )
                _, cancel_events = await asyncio.wait_for(
                    cancel_task, timeout=args.turn_timeout
                )
                if await target_page.evaluate("window.__skeinixClickCount") != 1:
                    raise AssertionError("cancelled Turn changed the page")
                if "cancel" not in _event_text(cancel_events).lower():
                    raise AssertionError("cancelled Turn did not emit a terminal cancel event")
                print("[ok] Stop cancelled the waiting Turn without releasing browser control")

                ended = (
                    await api.post(
                        "/api/v1/browser/debug/send",
                        {"cmd": "end_session", "args": {"reason": "e2e_cleanup"}},
                    )
                ).json()
                if not ended.get("observation", {}).get("ok"):
                    raise AssertionError(f"browser release failed: {ended!r}")
                released = (
                    await api.post(
                        "/api/v1/browser/debug/send",
                        {"cmd": "read_text", "args": {"tab": tab_id}},
                    )
                ).json()["observation"]
                if released.get("ok") or _browser_error_code(released) != "browser_session_released":
                    raise AssertionError(f"released-session error mismatch: {released!r}")
                invalid = (
                    await api.post(
                        "/api/v1/browser/debug/send",
                        {
                            "cmd": "start_session",
                            "args": {"target": "existing", "tab": 2_147_483_000},
                        },
                    )
                ).json()["observation"]
                if invalid.get("ok") or _browser_error_code(invalid) != "tab_not_found":
                    raise AssertionError(f"invalid-tab error mismatch: {invalid!r}")
                print("[ok] released-session and invalid-tab errors are structured")
    finally:
        if context is not None:
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
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--ws-base", default="ws://127.0.0.1:9001")
    parser.add_argument("--origin", default="http://127.0.0.1:9001")
    parser.add_argument("--fixture-port", type=int, default=9010)
    parser.add_argument("--turn-timeout", type=float, default=180)
    parser.add_argument(
        "--chromium",
        default=None,
        help="optional Chromium executable; defaults to Playwright's managed browser",
    )
    parser.add_argument(
        "--transport-only",
        action="store_true",
        help="verify the real extension WebSocket and chrome.debugger path without an LLM turn",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
