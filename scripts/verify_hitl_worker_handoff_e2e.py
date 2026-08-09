#!/usr/bin/env python3
"""Verify aged post-tool Continue gates across a real API worker replacement.

The original worker is terminated after the durable HITL row exists. A fresh
uvicorn process then serves the same product history and accepts the UI
Continue action, proving that neither Runtime relies on an old Python stack.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.async_api import BrowserContext, Page, async_playwright

from verify_sidepanel_resume_e2e import Api, DEFAULT_CHROMIUM, assert_one_user_bubble


MESSAGE_URL = re.compile(
    r"/api/v1/chat-scopes/(?P<scope>[^/]+)/chats/(?P<chat>[^/]+)/messages$"
)


async def wait_health(url: str, *, healthy: bool, timeout_s: float = 45) -> None:
    deadline = time.monotonic() + timeout_s
    async with httpx.AsyncClient(timeout=2) as client:
        while time.monotonic() < deadline:
            try:
                response = await client.get(url)
                current = response.status_code == 200
            except Exception:
                current = False
            if current is healthy:
                return
            await asyncio.sleep(0.2)
    raise AssertionError(f"health state did not become healthy={healthy}: {url}")


class NativeApiWorker:
    def __init__(
        self,
        *,
        run_dir: Path,
        python: Path,
        health_url: str,
    ) -> None:
        self.run_dir = run_dir
        self.python = python
        self.health_url = health_url
        self.pid_file = run_dir / "api.pid"
        self.process: subprocess.Popen[bytes] | None = None

    async def replace(self) -> tuple[int, int]:
        old_pid = int(self.pid_file.read_text().strip())
        with contextlib.suppress(ProcessLookupError):
            os.kill(old_pid, signal.SIGTERM)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                os.kill(old_pid, 0)
            except ProcessLookupError:
                break
            await asyncio.sleep(0.1)
        else:
            with contextlib.suppress(ProcessLookupError):
                os.kill(old_pid, signal.SIGKILL)
        await wait_health(self.health_url, healthy=False)
        new_pid = await self.start()
        if new_pid == old_pid:
            raise AssertionError("API worker PID did not change")
        return old_pid, new_pid

    async def start(self) -> int:
        run_script = self.run_dir / "run.sh"
        log_path = self.run_dir / "api.log"
        if not run_script.exists():
            raise RuntimeError(f"native run script is missing: {run_script}")
        log = log_path.open("ab", buffering=0)
        self.process = subprocess.Popen(
            [
                str(run_script),
                str(self.python),
                "-m",
                "uvicorn",
                "vibecanvas_api.app:build_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                "8000",
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.pid_file.write_text(f"{self.process.pid}\n")
        await wait_health(self.health_url, healthy=True)
        return self.process.pid

    async def ensure_running(self) -> None:
        try:
            await wait_health(self.health_url, healthy=True, timeout_s=2)
        except AssertionError:
            await self.start()


def backdate_hitl(hitl_request_id: str, *, hours: int, pg_port: int) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", hitl_request_id):
        raise ValueError(f"unsafe HITL id: {hitl_request_id!r}")
    sql = (
        "UPDATE hitl_requests "
        f"SET created_at = now() - interval '{max(1, hours)} hours' "
        f"WHERE hitl_request_id = '{hitl_request_id}' AND status = 'pending';"
    )
    subprocess.run(
        [
            "/usr/lib/postgresql/15/bin/psql",
            "-h",
            "localhost",
            "-p",
            str(pg_port),
            "-U",
            getpass.getuser(),
            "-d",
            "vibecanvas",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            sql,
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )


async def send_prompt(page: Page, prompt: str) -> tuple[str, str]:
    composer = page.locator('[data-role="agent-composer-input"]')
    send = page.locator('[data-action="agent-composer-send"]')
    await composer.wait_for(state="visible", timeout=30_000)
    await composer.fill(prompt)
    async with page.expect_request(
        lambda request: (
            request.method == "POST"
            and MESSAGE_URL.search(request.url) is not None
        ),
        timeout=30_000,
    ) as request_info:
        await asyncio.sleep(0.25)
        await send.click()
    request = await request_info.value
    match = MESSAGE_URL.search(request.url)
    if match is None:
        raise AssertionError(f"unexpected Turn URL: {request.url}")
    return match.group("scope"), match.group("chat")


async def exercise_runtime(
    *,
    runtime: str,
    page: Page,
    api: Api,
    worker: NativeApiWorker,
    age_hours: int,
    pg_port: int,
    turn_timeout: float,
) -> tuple[str, str]:
    response = await api.client.put(
        "/api/v1/agent-runtime/settings",
        headers=api.headers(),
        json={"default_runtime_type": runtime},
    )
    response.raise_for_status()
    await page.goto(
        f"{str(api.client.base_url).rstrip('/')}/chat",
        wait_until="domcontentloaded",
    )
    await page.get_by_role("heading", name=re.compile(r"^chat$", re.I)).wait_for(
        state="visible",
        timeout=20_000,
    )
    await page.locator('[data-action="chat-new"]').click()
    title = f"{runtime.title()} Worker Handoff"
    prompt = (
        "Call render_interactive exactly once and do not call any other tool. "
        f'Use title "{title}", require_human_confirm=true, and an html_preview '
        f'view whose exact HTML is <main><p id="marker">{runtime}-handoff-ready</p></main>. '
        "Do not emit prose after the tool call."
    )
    scope_id, chat_id = await send_prompt(page, prompt)
    card = page.locator('[data-role="interactive-artifact"]').filter(
        has_text=title
    ).last
    await card.locator('[data-action="interactive-submit"]').wait_for(
        state="visible",
        timeout=turn_timeout * 1_000,
    )
    pending = await api.get_json(f"/api/v1/chats/{chat_id}/hitl-requests")
    if len(pending) != 1:
        raise AssertionError(f"expected one pending HITL, got: {pending!r}")
    hitl_id = pending[0]["hitl_request_id"]
    backdate_hitl(hitl_id, hours=age_hours, pg_port=pg_port)

    old_pid, new_pid = await worker.replace()
    # Exercise the visible disconnected state before asking the new worker to
    # reconstruct history. The static web process remains alive throughout.
    await page.reload(wait_until="domcontentloaded")
    restored = page.locator('[data-role="interactive-artifact"]').filter(
        has_text=title
    ).last
    await restored.locator('[data-action="interactive-submit"]').wait_for(
        state="visible",
        timeout=60_000,
    )
    await assert_one_user_bubble(page, prompt)
    refreshed = await api.get_json(f"/api/v1/chats/{chat_id}/hitl-requests")
    if len(refreshed) != 1 or refreshed[0]["hitl_request_id"] != hitl_id:
        raise AssertionError(f"new worker reconstructed a different HITL: {refreshed!r}")

    await restored.locator('[data-action="interactive-submit"]').click()
    continued = restored.locator(
        '[data-action="interactive-submit"][data-state="continued"]'
    )
    await continued.wait_for(state="visible", timeout=30_000)
    if not await continued.is_disabled():
        raise AssertionError("Continue did not become disabled after acceptance")
    await page.locator('[data-action="agent-composer-send"]').wait_for(
        state="visible",
        timeout=turn_timeout * 1_000,
    )
    await assert_one_user_bubble(page, prompt)
    resolved = await api.get_json(f"/api/v1/hitl-requests/{hitl_id}")
    if resolved.get("status") != "submitted":
        raise AssertionError(f"Continue was not durably submitted: {resolved!r}")
    created_at = str(resolved.get("created_at") or "")
    if not created_at:
        raise AssertionError("aged HITL lost created_at")
    history = await api.get_json(
        f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}/messages?limit=200"
    )
    users = [
        item.get("content")
        for item in history.get("items", [])
        if item.get("role") == "user"
    ]
    if users != [prompt]:
        raise AssertionError(f"control Human message leaked into history: {users!r}")
    print(
        f"[ok] {runtime}: aged HITL resumed via UI after API {old_pid} -> {new_pid}",
        flush=True,
    )
    return scope_id, chat_id


async def run(args: argparse.Namespace) -> None:
    chromium = Path(args.chromium)
    if not chromium.exists():
        raise RuntimeError(f"Chromium executable is missing: {chromium}")
    api = Api(args.base_url)
    context: BrowserContext | None = None
    chats: list[tuple[str, str]] = []
    profile = tempfile.TemporaryDirectory(prefix="vibecanvas-hitl-handoff-")
    worker = NativeApiWorker(
        run_dir=Path(args.run_dir),
        python=Path(args.python),
        health_url=args.api_health,
    )
    try:
        await api.login()
        async with async_playwright() as playwright:
            context = await playwright.chromium.launch_persistent_context(
                profile.name,
                executable_path=str(chromium),
                headless=True,
                viewport={"width": 1440, "height": 960},
                args=["--no-sandbox"],
            )
            await context.add_init_script(
                f"""
                  localStorage.setItem('vibecanvas.token', {json.dumps(api.token)});
                  localStorage.setItem('vibecanvas.locale', 'en');
                """,
            )
            page = context.pages[0]
            for runtime in ("langchain", "codex"):
                chats.append(
                    await exercise_runtime(
                        runtime=runtime,
                        page=page,
                        api=api,
                        worker=worker,
                        age_hours=args.age_hours,
                        pg_port=args.pg_port,
                        turn_timeout=args.turn_timeout,
                    )
                )
    finally:
        await worker.ensure_running()
        if context is not None:
            with contextlib.suppress(Exception):
                await context.close()
        for scope_id, chat_id in chats:
            with contextlib.suppress(Exception):
                await api.delete(
                    f"/api/v1/chat-scopes/{scope_id}/chats/{chat_id}"
                )
        await api.close()
        profile.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument("--api-health", default="http://127.0.0.1:8000/healthz")
    parser.add_argument("--run-dir", default="/tmp/vibecanvas-native")
    parser.add_argument(
        "--python",
        default=sys.executable,
    )
    parser.add_argument("--pg-port", type=int, default=5433)
    parser.add_argument("--age-hours", type=int, default=6)
    parser.add_argument("--turn-timeout", type=float, default=240)
    parser.add_argument("--chromium", default=str(DEFAULT_CHROMIUM))
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
