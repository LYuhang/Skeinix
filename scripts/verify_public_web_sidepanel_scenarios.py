#!/usr/bin/env python3
"""Exercise realistic Skeinix side-panel tasks against public websites.

The verifier drives only the visible side-panel composer. The Agent discovers
and controls the public page through the product's browser tools; raw CDP is
reserved for opening the initial neutral tab and independently checking the
final page after browser control has been released.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from verify_all_command_browser_tools import _configure_runtime, _history
from verify_codex_browser_e2e import Api
from verify_visible_sidepanel_browser_scenarios import (
    EXTENSION_ID,
    RawCdp,
    _assert_vfs_png,
    _browser_cookie_auth,
    _new_sidepanel_chat,
    _scenario,
    _tool_media_entries,
)


WIKIPEDIA_URL = "https://en.wikipedia.org/wiki/Main_Page"
GITHUB_URL = "https://github.com/openai/openai-python"
HTTPBIN_FORM_URL = "https://httpbin.org/forms/post"


async def run(args: argparse.Namespace) -> None:
    api = Api(args.base_url, origin=args.origin)
    scope_id = ""
    chat_id = ""
    try:
        async with RawCdp(args.cdp_endpoint) as cdp:
            cookies = (await cdp.call("Storage.getCookies")).get("cookies") or []
            _browser_cookie_auth(cookies, api)
            if (await api.get("/api/v1/auth/me")).status_code != 200:
                raise RuntimeError("the visible Edge session is not authenticated")
            await _configure_runtime(api, args.runtime)
            bootstrap = (await api.get("/api/v1/chats/bootstrap?surface=browser")).json()
            scope_id = str(bootstrap["carrier_scope_id"])

            targets = await cdp.targets()
            panel = next(
                (
                    item for item in targets
                    if item.get("type") == "page"
                    and item.get("url") == f"chrome-extension://{EXTENSION_ID}/sidepanel.html"
                ),
                None,
            )
            iframe = next(
                (
                    item for item in targets
                    if item.get("type") == "iframe"
                    and "/embed/chat" in str(item.get("url") or "")
                ),
                None,
            )
            if panel is None or iframe is None:
                raise RuntimeError("Open the Skeinix side panel before acceptance.")
            iframe_session = await cdp.attach(str(iframe["targetId"]))

            public = next(
                (
                    item for item in targets
                    if item.get("type") == "page" and item.get("url") == WIKIPEDIA_URL
                ),
                None,
            )
            if public is None:
                created = await cdp.call("Target.createTarget", {"url": WIKIPEDIA_URL})
                public_target_id = str(created["targetId"])
            else:
                public_target_id = str(public["targetId"])
            await cdp.wait_target(
                lambda item: item.get("targetId") == public_target_id
                and "wikipedia.org" in str(item.get("url") or ""),
                timeout=30,
            )
            await cdp.activate(public_target_id)

            chat_id = await _new_sidepanel_chat(cdp, iframe_session)
            print(f"[{args.runtime}] public-web Chat ready: {chat_id}", flush=True)

            await _scenario(
                cdp,
                api,
                runtime=args.runtime,
                scope_id=scope_id,
                chat_id=chat_id,
                iframe_session=iframe_session,
                name="01-public-research",
                foreground_url="wikipedia.org",
                timeout_s=args.turn_timeout,
                instruction=(
                    "Act like a user researching a topic. Discover the currently open Wikipedia "
                    "tab with browser_tab(action='list_open'), start control of that exact existing "
                    "tab with require_user_auth=false, and check session health. Snapshot the page, "
                    "find the search input, search for 'Browser automation' by typing and pressing "
                    "Enter, wait for navigation, take a fresh snapshot, read the resulting article "
                    "heading and its first useful introductory paragraph, then take a viewport "
                    "screenshot. Keep browser control active for the next task. Use fresh handles "
                    "after navigation and adapt to the page if the exact selectors differ."
                ),
                required={
                    "browser_tab": 1,
                    "browser_start_session": 1,
                    "browser_session_status": 1,
                    "browser_snapshot": 2,
                    "browser_query": 1,
                    "browser_type": 1,
                    "browser_press_key": 1,
                    "browser_read_text": 2,
                    "browser_take_screenshot": 1,
                },
            )

            await _scenario(
                cdp,
                api,
                runtime=args.runtime,
                scope_id=scope_id,
                chat_id=chat_id,
                iframe_session=iframe_session,
                name="02-github-project-review",
                foreground_url="wikipedia.org",
                timeout_s=args.turn_timeout,
                instruction=(
                    f"Act like a developer evaluating an open-source dependency. Navigate the same "
                    f"controlled tab to {GITHUB_URL}, wait for the repository page, and snapshot it. "
                    "Read the repository heading and the visible README introduction. Locate the "
                    "License link using a fresh query, read its href, click it in the same tab, wait "
                    "for the license page, then read enough visible license text to identify the "
                    "license. Take a screenshot of the license page. Finally navigate back to the "
                    f"repository URL and keep control active. Do not sign in or modify the repository."
                ),
                required={
                    "browser_navigate": 2,
                    "browser_snapshot": 1,
                    "browser_read_text": 3,
                    "browser_query": 1,
                    "browser_get_attribute": 1,
                    "browser_click": 1,
                    "browser_take_screenshot": 1,
                },
            )

            await _scenario(
                cdp,
                api,
                runtime=args.runtime,
                scope_id=scope_id,
                chat_id=chat_id,
                iframe_session=iframe_session,
                name="03-public-form",
                foreground_url="github.com/openai/openai-python",
                timeout_s=args.turn_timeout,
                instruction=(
                    f"Act like a user asking for help completing a routine form. Navigate to "
                    f"{HTTPBIN_FORM_URL}, wait for the form, and snapshot it. Fill the customer name "
                    "with 'Skeinix Test User', choose the medium pizza size, choose the bacon topping, "
                    "and add the comment 'Public browser acceptance'. Submit the form, wait for the "
                    "response page, and read the body to confirm those exact dummy values were "
                    "submitted. Take a viewport screenshot, then end browser control with "
                    "require_user_auth=false. Never enter real personal data."
                ),
                required={
                    "browser_navigate": 1,
                    "browser_snapshot": 1,
                    "browser_type": 2,
                    "browser_click": 3,
                    "browser_read_text": 1,
                    "browser_take_screenshot": 1,
                    "browser_end_session": 1,
                },
            )

            binding = (await api.get(f"/api/v1/chats/{chat_id}/browser-binding")).json()
            if binding.get("status") != "inactive":
                raise AssertionError(f"browser session was not released: {binding!r}")

            final_target = await cdp.wait_target(
                lambda item: item.get("targetId") == public_target_id,
            )
            final_session = await cdp.attach(str(final_target["targetId"]))
            try:
                final_state = await cdp.evaluate(
                    final_session,
                    """(()=>({url:location.href,title:document.title,
                    body:(document.body?.innerText||'').slice(0,12000)}))()""",
                )
            finally:
                await cdp.detach(final_session)
            body = str(final_state.get("body") or "")
            for marker in ("Skeinix Test User", "Public browser acceptance"):
                if marker not in body:
                    raise AssertionError(
                        f"public form response omitted {marker!r}: {final_state!r}"
                    )

            history = await _history(api, scope_id, chat_id)
            workspace = (await api.get(f"/api/v1/chats/workspace?chat_id={chat_id}")).json()
            workspace_scope_id = str(workspace["workspace_scope_id"])
            screenshots = _tool_media_entries(history, "browser_take_screenshot")
            if len(screenshots) < 3:
                raise AssertionError(
                    f"expected three public-page screenshots, got {screenshots!r}"
                )
            verified: list[tuple[str, int]] = []
            for item in screenshots:
                path = str(item["path"])
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
                verified.append((path, size))
            print(
                f"[{args.runtime}] public form verified at {final_state.get('url')}",
                flush=True,
            )
            print(
                f"[{args.runtime}] public VFS screenshots verified: "
                + ", ".join(f"{path} ({size} bytes)" for path, size in verified),
                flush=True,
            )
            print("visible_sidepanel_public_web_acceptance=pass", flush=True)
    finally:
        if scope_id and chat_id and not args.keep_chat:
            with contextlib.suppress(Exception):
                await api.delete(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}?surface=browser"
                )
        await api.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", choices=("langchain", "codex"), default="codex")
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--origin", default="http://127.0.0.1:9001")
    parser.add_argument("--cdp-endpoint", default="http://127.0.0.1:9225")
    parser.add_argument("--turn-timeout", type=float, default=360)
    parser.add_argument("--keep-chat", action="store_true")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
