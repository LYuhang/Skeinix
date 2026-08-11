#!/usr/bin/env python3
"""Visible Windows Edge acceptance without competing for the controlled tab.

Playwright/CDP normally auto-attaches every page when connecting to an existing
browser. That conflicts with the MV3 extension's own ``chrome.debugger`` owner
and can also hang on stale targets. This verifier intentionally uses raw CDP:

* browser-level commands discover and foreground tabs without attaching them;
* only the extension side-panel page and its app iframe are attached for UI work;
* the controlled target page is attached only after ``browser_end_session``;
* final DOM state and a screenshot independently prove the real page changed.

All Agent instructions are entered through the visible side-panel composer. The
Agent itself must discover the live tab, observe the page, act, re-observe, and
release control. Tool cards alone never count as acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import io
import json
import urllib.request
from pathlib import Path
from typing import Any, Callable

import websockets
from PIL import Image

from verify_all_command_browser_tools import (
    TOOLS,
    _assert_completed_calls,
    _configure_runtime,
    _history,
    _tool_counts,
)
from verify_codex_browser_e2e import Api, FixtureServer


EXTENSION_ID = "mkfldhmlgdbpmhplaphhcfcdcoaakcik"
REPO_ROOT = Path(__file__).resolve().parents[1]


class RawCdp:
    def __init__(self, endpoint: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.ws: Any = None
        self._next_id = 0

    def _json_get(self, path: str) -> dict[str, Any]:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"{self.endpoint}{path}", timeout=5) as response:
            return json.load(response)

    async def __aenter__(self) -> RawCdp:
        version = self._json_get("/json/version")
        self.ws = await websockets.connect(
            version["webSocketDebuggerUrl"],
            open_timeout=5,
            close_timeout=2,
        )
        return self

    async def __aexit__(self, *_args: object) -> None:
        if self.ws is not None:
            await self.ws.close()

    async def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
        timeout: float = 10,
    ) -> dict[str, Any]:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        if session_id:
            message["sessionId"] = session_id
        await self.ws.send(json.dumps(message))
        while True:
            response = json.loads(await asyncio.wait_for(self.ws.recv(), timeout))
            if response.get("id") != self._next_id:
                continue
            if "error" in response:
                raise RuntimeError(f"CDP {method} failed: {response['error']}")
            return dict(response.get("result") or {})

    async def targets(self) -> list[dict[str, Any]]:
        result = await self.call("Target.getTargets")
        return list(result.get("targetInfos") or [])

    async def wait_target(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        *,
        timeout: float = 15,
    ) -> dict[str, Any]:
        deadline = asyncio.get_running_loop().time() + timeout
        last: list[dict[str, Any]] = []
        while asyncio.get_running_loop().time() < deadline:
            last = await self.targets()
            match = next((target for target in last if predicate(target)), None)
            if match is not None:
                return match
            await asyncio.sleep(0.1)
        compact = [
            {"type": item.get("type"), "url": item.get("url")}
            for item in last
        ]
        raise AssertionError(f"expected browser target did not appear: {compact!r}")

    async def attach(self, target_id: str) -> str:
        result = await self.call(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        return str(result["sessionId"])

    async def detach(self, session_id: str) -> None:
        with contextlib.suppress(Exception):
            await self.call("Target.detachFromTarget", {"sessionId": session_id})

    async def evaluate(
        self,
        session_id: str,
        expression: str,
        *,
        await_promise: bool = False,
        user_gesture: bool = False,
        timeout: float = 10,
    ) -> Any:
        result = await self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": await_promise,
                "returnByValue": True,
                "userGesture": user_gesture,
            },
            session_id=session_id,
            timeout=timeout,
        )
        if result.get("exceptionDetails"):
            raise RuntimeError(
                f"browser evaluation failed: {result['exceptionDetails']}"
            )
        return (result.get("result") or {}).get("value")

    async def activate(self, target_id: str) -> None:
        await self.call("Target.activateTarget", {"targetId": target_id})


def _browser_cookie_auth(cookies: list[dict[str, Any]], api: Api) -> None:
    values = {str(item.get("name") or ""): str(item.get("value") or "") for item in cookies}
    session_name = next((name for name in values if name.endswith("-web-session")), "")
    csrf_name = next((name for name in values if name.endswith("-web-csrf")), "")
    if not session_name or not csrf_name:
        raise RuntimeError("Sign in to Skeinix in this Edge profile before acceptance.")
    api.cookie_header = (
        f"{session_name}={values[session_name]}; {csrf_name}={values[csrf_name]}"
    )
    api.csrf_token = values[csrf_name]


def _tool_media_entries(
    history: list[dict[str, Any]],
    tool_name: str,
) -> list[dict[str, Any]]:
    """Read materialized media metadata from durable tool-result envelopes."""
    call_ids: set[str] = set()
    for message in history:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            function = call.get("function")
            name = (
                function.get("name")
                if isinstance(function, dict)
                else call.get("name") or call.get("tool_name")
            )
            if name == tool_name:
                call_ids.add(str(call.get("id") or call.get("tool_call_id") or ""))

    entries: list[dict[str, Any]] = []
    for message in history:
        if message.get("role") != "tool" or str(message.get("tool_call_id") or "") not in call_ids:
            continue
        raw = message.get("content")
        try:
            envelope = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            continue
        if not isinstance(envelope, dict):
            continue
        structured = envelope.get("structuredContent")
        artifact = structured.get("artifact") if isinstance(structured, dict) else None
        auxiliary = artifact.get("auxiliary") if isinstance(artifact, dict) else None
        for item in auxiliary if isinstance(auxiliary, list) else []:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                entries.append(dict(item))
    return entries


async def _assert_vfs_png(
    api: Api,
    *,
    workspace_scope_id: str,
    path: str,
    declared_bytes: int | None = None,
) -> int:
    """Prove a VFS descriptor and its object-store bytes are a real PNG."""
    descriptor = (
        await api.get(
            "/api/v1/vfs/content",
            params={"wf_id": workspace_scope_id, "path": path},
        )
    ).json()
    size = int(descriptor.get("size_bytes") or 0)
    if size <= 0:
        raise AssertionError(f"VFS media is empty: {path}")
    if str(descriptor.get("content_type") or "").split(";", 1)[0] != "image/png":
        raise AssertionError(f"VFS media has the wrong MIME: {descriptor!r}")
    if declared_bytes is not None and declared_bytes != size:
        raise AssertionError(
            f"tool/VFS byte counts disagree for {path}: {declared_bytes} != {size}"
        )
    signed = (
        await api.post(
            "/api/v1/vfs/sign",
            {"wf_id": workspace_scope_id, "path": path},
        )
    ).json()
    raw = await api.client.get(str(signed["url"]))
    raw.raise_for_status()
    if len(raw.content) != size:
        raise AssertionError(
            f"signed VFS bytes disagree with descriptor for {path}: "
            f"{len(raw.content)} != {size}"
        )
    if not raw.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AssertionError(f"VFS media is not a decodable PNG payload: {path}")
    try:
        with Image.open(io.BytesIO(raw.content)) as image:
            width, height = image.size
            image_format = image.format
            image.verify()
    except Exception as exc:
        raise AssertionError(f"VFS PNG decode failed for {path}: {exc}") from exc
    if image_format != "PNG" or width <= 0 or height <= 0:
        raise AssertionError(
            f"VFS media has invalid PNG dimensions/format: {path} "
            f"({image_format}, {width}x{height})"
        )
    return size


async def _new_sidepanel_chat(cdp: RawCdp, iframe_session: str) -> str:
    previous = await cdp.evaluate(
        iframe_session,
        "document.querySelector('[data-role=\"agent-composer-input\"]')?.dataset.chatId || ''",
    )
    await cdp.evaluate(
        iframe_session,
        "document.querySelector('[data-action=\"agent-sidebar-new-chat\"]')?.click()",
        user_gesture=True,
    )
    deadline = asyncio.get_running_loop().time() + 20
    while asyncio.get_running_loop().time() < deadline:
        chat_id = await cdp.evaluate(
            iframe_session,
            "document.querySelector('[data-role=\"agent-composer-input\"]')?.dataset.chatId || ''",
        )
        if chat_id and chat_id != previous:
            return str(chat_id)
        await asyncio.sleep(0.15)
    raise AssertionError("side-panel New Chat did not expose a fresh Chat id")


async def _composer_state(cdp: RawCdp, iframe_session: str) -> dict[str, Any]:
    value = await cdp.evaluate(
        iframe_session,
        """(()=>{const c=document.querySelector('[data-role="agent-composer-input"]');
        return {exists:!!c,disabled:!!c?.disabled,value:c?.value||'',
        stopping:!!document.querySelector('[data-action="agent-composer-stop"]')};})()""",
    )
    return dict(value or {})


async def _send_sidepanel_message(
    cdp: RawCdp,
    iframe_session: str,
    content: str,
) -> int:
    deadline = asyncio.get_running_loop().time() + 8
    while asyncio.get_running_loop().time() < deadline:
        state = await _composer_state(cdp, iframe_session)
        if state.get("exists") and not state.get("disabled") and not state.get("stopping"):
            await asyncio.sleep(0.4)
            state = await _composer_state(cdp, iframe_session)
            if not state.get("disabled") and not state.get("stopping"):
                break
        await asyncio.sleep(0.1)
    else:
        raise AssertionError("side-panel composer did not become stably idle")

    payload = json.dumps(content)
    fill_expression = f"""(()=>{{
      const composer=document.querySelector('[data-role="agent-composer-input"]');
      if(!composer) throw new Error('composer missing');
      const setter=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;
      setter.call(composer,{payload});
      composer.dispatchEvent(new Event('input',{{bubbles:true}}));
      return true;
    }})()"""
    started = asyncio.get_running_loop().time()
    await cdp.evaluate(
        iframe_session,
        fill_expression,
    )
    # Let React commit the controlled textarea state before the click. Keeping
    # this as two synchronous evaluations avoids a cross-frame Promise that Edge
    # may collect when the Chat query invalidates after a completed turn.
    await asyncio.sleep(0.05)
    await cdp.evaluate(
        iframe_session,
        """(()=>{const send=document.querySelector('[data-action="agent-composer-send"]');
        if(!send||send.disabled)throw new Error('send unavailable');send.click();return true;})()""",
        user_gesture=True,
    )
    clear_deadline = started + 1
    while asyncio.get_running_loop().time() < clear_deadline:
        if not (await _composer_state(cdp, iframe_session)).get("value"):
            return int((asyncio.get_running_loop().time() - started) * 1000)
        await asyncio.sleep(0.01)
    raise AssertionError("composer did not clear within one second of Send")


async def _scenario(
    cdp: RawCdp,
    api: Api,
    *,
    runtime: str,
    scope_id: str,
    chat_id: str,
    iframe_session: str,
    name: str,
    instruction: str,
    required: dict[str, int],
    foreground_url: str,
    timeout_s: float,
) -> list[dict[str, Any]]:
    before = await _history(api, scope_id, chat_id)
    counts = _tool_counts(before)
    target = await cdp.wait_target(
        lambda item: item.get("type") == "page"
        and foreground_url in str(item.get("url") or ""),
    )
    await cdp.activate(str(target["targetId"]))
    handoff_ms = await _send_sidepanel_message(cdp, iframe_session, f"/browser {instruction}")
    print(f"[{runtime}] {name}: composer handoff={handoff_ms}ms", flush=True)

    deadline = asyncio.get_running_loop().time() + timeout_s
    last_error: Exception | None = None
    while asyncio.get_running_loop().time() < deadline:
        try:
            history = await _history(api, scope_id, chat_id)
            _assert_completed_calls(
                history,
                before_counts=counts,
                required_increments=required,
            )
            state = await _composer_state(cdp, iframe_session)
            if state.get("disabled") or state.get("stopping"):
                raise AssertionError("Agent turn is still active")
            await asyncio.sleep(0.5)
            state = await _composer_state(cdp, iframe_session)
            if state.get("disabled") or state.get("stopping"):
                raise AssertionError("composer did not remain idle")
            print(f"[{runtime}] {name}: Agent tool chain complete", flush=True)
            return history
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.4)
    raise AssertionError(f"{name}: side-panel turn did not complete: {last_error}")


async def run(args: argparse.Namespace) -> None:
    runtime = args.runtime
    fixture_base = f"http://127.0.0.1:{args.fixture_port}"
    initial_url = f"{fixture_base}/detail.html"
    search_url = f"{fixture_base}/search.html"
    form_url = f"{fixture_base}/page.html"
    search_term = "Skeinix browser automation"
    marker = f"SKEINIX_FORM_OK_{runtime.upper()}"
    resource_path = f"/data/browser-acceptance/{runtime}-pixel.png"
    api = Api(args.base_url, origin=args.origin)
    scope_id = ""
    chat_id = ""
    evidence_dir = Path(args.evidence_dir).resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    try:
        with FixtureServer(args.fixture_port):
            async with RawCdp(args.cdp_endpoint) as cdp:
                cookies = (await cdp.call("Storage.getCookies")).get("cookies") or []
                _browser_cookie_auth(cookies, api)
                me = await api.get("/api/v1/auth/me")
                if me.status_code != 200:
                    raise RuntimeError("the visible Edge session is not authenticated")
                await _configure_runtime(api, runtime)
                bootstrap = (await api.get("/api/v1/chats/bootstrap?surface=browser")).json()
                scope_id = str(bootstrap["carrier_scope_id"])

                targets = await cdp.targets()
                panel = next(
                    (
                        item
                        for item in targets
                        if item.get("type") == "page"
                        and item.get("url")
                        == f"chrome-extension://{EXTENSION_ID}/sidepanel.html"
                    ),
                    None,
                )
                iframe = next(
                    (
                        item
                        for item in targets
                        if item.get("type") == "iframe"
                        and "/embed/chat" in str(item.get("url") or "")
                    ),
                    None,
                )
                if panel is None or iframe is None:
                    raise RuntimeError("Open the Skeinix side panel before acceptance.")
                panel_session = await cdp.attach(str(panel["targetId"]))
                iframe_session = await cdp.attach(str(iframe["targetId"]))

                await cdp.call("Target.createTarget", {"url": initial_url})
                await cdp.wait_target(
                    lambda item: item.get("type") == "page"
                    and item.get("url") == initial_url,
                )
                tab_id = await cdp.evaluate(
                    panel_session,
                    f"""chrome.tabs.query({{}}).then(tabs=>{{const tab=tabs.find(x=>x.url==={json.dumps(initial_url)});return tab?.id||0;}})""",
                    await_promise=True,
                )
                if not int(tab_id or 0):
                    raise AssertionError("new fixture tab has no Chrome tab id")
                chat_id = await _new_sidepanel_chat(cdp, iframe_session)
                print(f"[{runtime}] visible side-panel Chat ready: {chat_id}", flush=True)

                await _scenario(
                    cdp,
                    api,
                    runtime=runtime,
                    scope_id=scope_id,
                    chat_id=chat_id,
                    iframe_session=iframe_session,
                    name="01-discover-connect-observe",
                    foreground_url=initial_url,
                    timeout_s=args.turn_timeout,
                    instruction=(
                        f"Discover the live tab for {initial_url} with browser_tab action='list_open' "
                        "instead of trusting an old tab id. Start control over the matching existing "
                        "tab with require_user_auth=false, inspect browser_session_status, navigate to "
                        f"{search_url}, snapshot body, and take a viewport screenshot. Keep the session "
                        "active. If health reports stale state or a conflict, follow its recommended "
                        "action safely; never detach an unknown debugger."
                    ),
                    required={
                        "browser_tab": 1,
                        "browser_start_session": 1,
                        "browser_session_status": 1,
                        "browser_navigate": 1,
                        "browser_snapshot": 1,
                        "browser_take_screenshot": 1,
                    },
                )
                await cdp.wait_target(lambda item: item.get("url") == search_url)

                await _scenario(
                    cdp,
                    api,
                    runtime=runtime,
                    scope_id=scope_id,
                    chat_id=chat_id,
                    iframe_session=iframe_session,
                    name="02-search-open-result",
                    foreground_url=search_url,
                    timeout_s=args.turn_timeout,
                    instruction=(
                        f"On the controlled search page, search for {search_term!r}. Query "
                        "#search-input, type with replace=true, press Enter expecting #search-results, "
                        "wait for results, take a fresh snapshot, query #primary-result, read its text "
                        "and href, click it expecting #detail-title, wait for the detail, read "
                        "#acceptance-trail, inspect #detail-content as markdown, and take a screenshot. "
                        "Use fresh handles after navigation and leave the detail page visible."
                    ),
                    required={
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
                detail_target = await cdp.wait_target(
                    lambda item: "/detail.html?from=Skeinix%20browser%20automation"
                    in str(item.get("url") or "")
                )
                await cdp.activate(str(detail_target["targetId"]))

                await _scenario(
                    cdp,
                    api,
                    runtime=runtime,
                    scope_id=scope_id,
                    chat_id=chat_id,
                    iframe_session=iframe_session,
                    name="03-form-resource-scroll",
                    foreground_url="/detail.html?from=",
                    timeout_s=args.turn_timeout,
                    instruction=(
                        f"Navigate the same controlled tab to {form_url}. Snapshot it and query "
                        "#reason,#decision,#submit,#thumb,#bottom. Type "
                        f"{marker!r} into #reason with replace=true, select 'reject', click #submit "
                        "expecting #clicked, press Enter on #reason expecting #keyed, wait for the "
                        f"markers, fetch #thumb to {resource_path!r}, scroll #bottom into view, take "
                        "a screenshot, and check login. Re-observe as needed and keep control."
                    ),
                    required={
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
                await cdp.wait_target(lambda item: item.get("url") == form_url)

                await _scenario(
                    cdp,
                    api,
                    runtime=runtime,
                    scope_id=scope_id,
                    chat_id=chat_id,
                    iframe_session=iframe_session,
                    name="04-tabs-release",
                    foreground_url=form_url,
                    timeout_s=args.turn_timeout,
                    instruction=(
                        "Query and click #open-detail to open its target=_blank page. Wait for the new "
                        "tab, list controlled tabs, switch to the returned detail tab, snapshot and "
                        "read #detail-title there, close only that excursion, list tabs again, and "
                        "finally call browser_end_session with require_user_auth=false. Do not close "
                        "the root form tab."
                    ),
                    required={
                        "browser_query": 1,
                        "browser_click": 1,
                        "browser_tab": 4,
                        "browser_snapshot": 1,
                        "browser_read_text": 1,
                        "browser_end_session": 1,
                    },
                )

                released = (await api.get(f"/api/v1/chats/{chat_id}/browser-binding")).json()
                if released.get("status") != "inactive":
                    raise AssertionError(f"browser session was not released: {released!r}")

                # Only now may the independent verifier attach to the formerly
                # controlled page. Doing this earlier would itself be the
                # external-debugger conflict the product is designed to report.
                root = await cdp.wait_target(lambda item: item.get("url") == form_url)
                root_session = await cdp.attach(str(root["targetId"]))
                try:
                    final_state = await cdp.evaluate(
                        root_session,
                        """(()=>({url:location.href,title:document.title,
                        reason:document.querySelector('#reason')?.value||'',
                        decision:document.querySelector('#decision')?.value||'',
                        clicked:document.querySelector('#clicked')?.textContent||'',
                        keyed:document.querySelector('#keyed')?.textContent||'',
                        scrollY:window.scrollY}))()""",
                    )
                    expected = {
                        "reason": marker,
                        "decision": "reject",
                        "clicked": "Clicked 1",
                        "keyed": "Enter observed",
                    }
                    for key, value in expected.items():
                        if final_state.get(key) != value:
                            raise AssertionError(
                                f"independent final page check failed for {key}: {final_state!r}"
                            )
                    if int(final_state.get("scrollY") or 0) <= 0:
                        raise AssertionError(f"page did not remain scrolled: {final_state!r}")
                    shot = await cdp.call(
                        "Page.captureScreenshot",
                        {"format": "png", "fromSurface": True},
                        session_id=root_session,
                    )
                    (evidence_dir / f"{runtime}-final-page.png").write_bytes(
                        base64.b64decode(shot["data"])
                    )
                finally:
                    await cdp.detach(root_session)

                history = await _history(api, scope_id, chat_id)
                workspace = (await api.get(f"/api/v1/chats/workspace?chat_id={chat_id}")).json()
                workspace_scope_id = str(workspace["workspace_scope_id"])
                screenshots = _tool_media_entries(history, "browser_take_screenshot")
                if len(screenshots) < 3:
                    raise AssertionError(
                        f"expected at least three durable screenshot artifacts, got {screenshots!r}"
                    )
                verified_screenshots: list[tuple[str, int]] = []
                for item in screenshots:
                    path = str(item["path"])
                    if not path.startswith("/data/browser-media/"):
                        raise AssertionError(f"screenshot escaped browser-media VFS: {path}")
                    size = await _assert_vfs_png(
                        api,
                        workspace_scope_id=workspace_scope_id,
                        path=path,
                        declared_bytes=(
                            int(item["bytes_len"])
                            if item.get("bytes_len") is not None
                            else None
                        ),
                    )
                    verified_screenshots.append((path, size))

                resource_size = await _assert_vfs_png(
                    api,
                    workspace_scope_id=workspace_scope_id,
                    path=resource_path,
                )
                print(
                    f"[{runtime}] VFS media verified: {len(verified_screenshots)} "
                    f"screenshot(s), resource={resource_size} bytes",
                    flush=True,
                )

                counts = _tool_counts(history)
                missing = [name for name in TOOLS if counts.get(name, 0) < 1]
                if missing:
                    raise AssertionError(f"public browser tools not covered: {missing}")
                print(
                    f"visible_sidepanel_browser_acceptance_{runtime}=pass ({len(TOOLS)}/19)",
                    flush=True,
                )
    finally:
        if scope_id and chat_id and not args.keep_chat:
            with contextlib.suppress(Exception):
                await api.delete(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}?surface=browser"
                )
        await api.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=("langchain", "codex"), required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--origin", default="http://127.0.0.1:9001")
    parser.add_argument("--cdp-endpoint", default="http://127.0.0.1:9225")
    parser.add_argument("--fixture-port", type=int, default=9011)
    parser.add_argument("--turn-timeout", type=float, default=360)
    parser.add_argument(
        "--evidence-dir",
        default=str(
            REPO_ROOT
            / "docs/internal/evidence/browser-extension-2026-08-11/visible-sidepanel"
        ),
    )
    parser.add_argument("--keep-chat", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
