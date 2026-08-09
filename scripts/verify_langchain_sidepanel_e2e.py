#!/usr/bin/env python3
"""Real MV3 side-panel smoke for LangChain post-tool Continue recovery."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import tempfile
import uuid
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright

from verify_sidepanel_resume_e2e import (
    Api,
    DEFAULT_CHROMIUM,
    EXTENSION_ID,
    assert_one_user_bubble,
    send_and_capture,
    wait_for_embed,
    wait_turn_idle,
)


async def run(args: argparse.Namespace) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    extension_dist = repo_root / "extension" / "dist"
    chromium = Path(args.chromium)
    if not (extension_dist / "manifest.json").exists():
        raise RuntimeError("extension/dist is missing")
    if not chromium.exists():
        raise RuntimeError(f"Chromium executable is missing: {chromium}")

    api = Api(args.base_url)
    context: BrowserContext | None = None
    profile = tempfile.TemporaryDirectory(prefix="vibecanvas-langchain-sidepanel-")
    console: list[str] = []
    network: list[str] = []
    chat: tuple[str, str] | None = None
    try:
        await api.login()
        response = await api.client.put(
            "/api/v1/agent-runtime/settings",
            headers=api.headers(),
            json={"default_runtime_type": "langchain"},
        )
        response.raise_for_status()

        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                profile.name,
                executable_path=str(chromium),
                headless=True,
                viewport={"width": 430, "height": 900},
                args=[
                    f"--disable-extensions-except={extension_dist}",
                    f"--load-extension={extension_dist}",
                    "--no-sandbox",
                ],
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
                lambda message: console.append(f"{message.type}: {message.text}"),
            )
            panel.on("pageerror", lambda error: console.append(f"pageerror: {error}"))
            panel.on(
                "request",
                lambda request: network.append(
                    f"{request.method} {request.url} {request.post_data or ''}"
                )
                if request.method == "POST"
                else None,
            )
            await panel.goto(
                f"chrome-extension://{EXTENSION_ID}/sidepanel.html",
                wait_until="domcontentloaded",
            )
            frame = await wait_for_embed(panel, console)
            suffix = uuid.uuid4().hex[:8]
            title = f"LangChain Sidepanel Acceptance {suffix}"
            prompt = (
                "Call render_interactive exactly once and do not call any other tool. "
                f'Use title "{title}", '
                "require_human_confirm=true, and an html_preview view whose exact "
                'HTML is <main><p id="marker">sidepanel-ready</p></main>. '
                "Do not emit prose after the tool call."
            )
            scope_id, chat_id, payload = await send_and_capture(
                panel,
                frame,
                prompt,
                network,
                keyboard_submit=True,
            )
            chat = (scope_id, chat_id)
            if payload.get("surface") != "sidepanel":
                raise AssertionError(f"wrong surface in LangChain Turn: {payload!r}")

            card = frame.locator('[data-role="interactive-artifact"]').filter(
                has_text=title,
            ).last
            await card.locator('[data-action="interactive-submit"]').wait_for(
                state="visible",
                timeout=args.turn_timeout * 1_000,
            )
            await card.frame_locator("iframe").locator("#marker").wait_for(
                state="visible",
                timeout=30_000,
            )
            pending_before = await api.get_json(
                f"/api/v1/chats/{chat_id}/hitl-requests"
            )
            if len(pending_before) != 1:
                raise AssertionError(
                    f"expected one durable Sidepanel HITL: {pending_before!r}"
                )
            hitl_id = pending_before[0]["hitl_request_id"]
            print("[ok] LangChain Platform MCP rendered in the real side panel", flush=True)

            await context.set_offline(True)
            await asyncio.sleep(1)
            await context.set_offline(False)
            await panel.reload(wait_until="domcontentloaded")
            frame = await wait_for_embed(panel, console)
            pending_after = await api.get_json(
                f"/api/v1/chats/{chat_id}/hitl-requests"
            )
            if (
                len(pending_after) != 1
                or pending_after[0].get("hitl_request_id") != hitl_id
            ):
                raise AssertionError(
                    f"reload changed the durable Sidepanel HITL: {pending_after!r}"
                )
            try:
                await assert_one_user_bubble(frame, prompt)
            except AssertionError as exc:
                history = await api.get_json(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
                    "?limit=200"
                )
                screenshot = Path(args.screenshot).with_name(
                    "vibecanvas-langchain-sidepanel-chat-restore-failure.png"
                )
                await panel.screenshot(path=str(screenshot), full_page=True)
                visible = (await frame.locator("body").inner_text())[:8_000]
                raise AssertionError(
                    f"{exc}; durable_history={history!r}; visible={visible!r}; "
                    f"console={console[-50:]!r}; screenshot={screenshot}"
                ) from exc
            restored = frame.locator('[data-role="interactive-artifact"]').filter(
                has_text=title,
            ).last
            try:
                await restored.locator(
                    '[data-action="interactive-submit"]'
                ).wait_for(state="visible", timeout=60_000)
            except Exception as exc:
                history = await api.get_json(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages"
                    "?limit=200"
                )
                screenshot = Path(args.screenshot).with_name(
                    "vibecanvas-langchain-sidepanel-card-restore-failure.png"
                )
                await panel.screenshot(path=str(screenshot), full_page=True)
                visible = (await frame.locator("body").inner_text())[:8_000]
                raise AssertionError(
                    f"durable HITL {hitl_id} exists but card was not restored; "
                    f"history={history!r}; visible={visible!r}; "
                    f"console={console[-50:]!r}; screenshot={screenshot}"
                ) from exc
            print("[ok] offline/reload restored the same post-tool Continue gate", flush=True)

            history_before_continue = await api.get_json(
                f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages?limit=200"
            )
            visible_users_before = [
                item.get("content")
                for item in history_before_continue.get("items", [])
                if item.get("role") == "user"
            ]
            if visible_users_before.count(prompt) != 1:
                raise AssertionError(
                    "current acceptance prompt was not projected exactly once "
                    f"before Continue: {visible_users_before!r}"
                )
            await restored.locator('[data-action="interactive-submit"]').click()
            continued = restored.locator(
                '[data-action="interactive-submit"][data-state="continued"]'
            )
            await continued.wait_for(state="visible", timeout=30_000)
            if not await continued.is_disabled():
                raise AssertionError("clicked Continue did not become disabled")
            await wait_turn_idle(frame, args.turn_timeout)
            await assert_one_user_bubble(frame, prompt)
            resolved = await api.get_json(f"/api/v1/hitl-requests/{hitl_id}")
            if resolved.get("status") != "submitted":
                raise AssertionError(
                    f"Continue click was not persisted: {resolved!r}"
                )
            await panel.reload(wait_until="domcontentloaded")
            frame = await wait_for_embed(panel, console)
            persisted_card = frame.locator(
                '[data-role="interactive-artifact"]'
            ).filter(has_text=title).last
            persisted_continue = persisted_card.locator(
                '[data-action="interactive-submit"][data-state="continued"]'
            )
            await persisted_continue.wait_for(state="visible", timeout=60_000)
            if not await persisted_continue.is_disabled():
                raise AssertionError(
                    "reload revived an already-clicked Continue action"
                )
            history = await api.get_json(
                f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages?limit=200"
            )
            product_users = [
                item.get("content")
                for item in history.get("items", [])
                if item.get("role") == "user"
            ]
            if product_users != visible_users_before:
                raise AssertionError(
                    "hidden Continue control changed product-visible user history: "
                    f"before={visible_users_before!r}; after={product_users!r}"
                )
            print(
                "[ok] UI Continue created one hidden Turn and restored disabled after reload",
                flush=True,
            )

            fatal = [
                line
                for line in console
                if "pageerror:" in line
                or "uncaught" in line.lower()
                or "useLocation() may be used only" in line
            ]
            if fatal:
                raise AssertionError(f"LangChain side-panel console errors: {fatal!r}")
            screenshot = Path(args.screenshot)
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            await panel.screenshot(path=str(screenshot), full_page=True)
    finally:
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
        if chat is not None:
            scope_id, chat_id = chat
            with contextlib.suppress(Exception):
                await api.delete(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}?surface=browser"
                )
        await api.close()
        profile.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--turn-timeout", type=float, default=180)
    parser.add_argument("--chromium", default=str(DEFAULT_CHROMIUM))
    parser.add_argument(
        "--screenshot",
        default="/tmp/vibecanvas-langchain-sidepanel-e2e.png",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
