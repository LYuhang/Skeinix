#!/usr/bin/env python3
"""Exhaustive real Chat -> Browser MCP -> MV3 extension acceptance journey.

Run one Runtime at a time after a clean application restart. Playwright only
hosts and observes the real page/extension; every target operation is initiated
by an Agent tool call through the public Chat contract.
"""

from __future__ import annotations

import argparse
import asyncio
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
    DEFAULT_CHROMIUM,
    FixtureServer,
    _browser_error_code,
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
    payload = (
        await api.get(
            f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
            "?limit=500&tail=true"
        )
    ).json()
    return list(payload.get("items") or [])


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
    extension_dist = REPO_ROOT / "extension" / "dist"
    if not (extension_dist / "manifest.json").is_file():
        raise RuntimeError("extension/dist is missing; build the extension first")
    chromium = Path(args.chromium)
    if not chromium.is_file():
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
    try:
        await api.login()
        model_id = await _configure_runtime(api, runtime)
        bootstrap = (await api.get("/api/v1/chats/bootstrap?surface=browser")).json()
        scope_id = str(bootstrap["carrier_scope_id"])
        if "browser" not in bootstrap.get("available_commands", []):
            raise AssertionError("Browser surface did not advertise /browser")

        with FixtureServer(args.fixture_port):
            async with async_playwright() as playwright:
                context = await playwright.chromium.launch_persistent_context(
                    profile.name,
                    executable_path=str(chromium),
                    headless=True,
                    args=[
                        f"--disable-extensions-except={extension_dist}",
                        f"--load-extension={extension_dist}",
                        "--no-sandbox",
                    ],
                )
                target_page = context.pages[0]
                await target_page.goto(initial_url)
                await target_page.wait_for_load_state("domcontentloaded")
                control_page = await _extension_page(context)
                tab_id = await _connect_extension(
                    api, control_page, initial_url, args.ws_base,
                )
                print(f"[{runtime}] extension connected to real tab {tab_id}", flush=True)

                async def invoke(tool: str, instruction: str) -> dict[str, Any]:
                    return await _invoke(
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
                await _install_fixture_behaviour(target_page)

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
                    await api.post(
                        "/api/v1/browser/debug/send",
                        {"cmd": "read_text", "args": {"tab": tab_id}},
                    )
                ).json()["observation"]
                if released.get("ok") or _browser_error_code(released) != "browser_session_released":
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
    parser.add_argument("--runtime", choices=("langchain", "codex"), required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--ws-base", default="ws://127.0.0.1:9001")
    parser.add_argument("--origin", default="http://127.0.0.1:9001")
    parser.add_argument("--fixture-port", type=int, default=9010)
    parser.add_argument("--turn-timeout", type=float, default=360)
    parser.add_argument("--chromium", default=str(DEFAULT_CHROMIUM))
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
