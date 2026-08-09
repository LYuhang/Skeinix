#!/usr/bin/env python3
"""Real-browser reconnaissance for the full application acceptance run.

This is intentionally a browser probe, not a mock-based UI test.  It signs in
through the rendered login form, visits each static production surface, records
console/request failures, and saves screenshots for visual inspection.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright


PUBLIC_ROUTES = (
    ("login", "/login"),
    ("signup", "/signup"),
    ("reset-password", "/reset-password"),
)

AUTHENTICATED_ROUTES = (
    ("chat", "/chat"),
    ("workspace", "/workspace"),
    ("tasks", "/tasks"),
    ("deployments", "/deployments"),
    ("credentials", "/credentials"),
    ("mcp-servers", "/mcp-servers"),
    ("platform-mcp-browser", "/mcp-servers/platform/browser"),
    ("skills", "/skills"),
    ("knowledge", "/knowledge"),
    ("storage", "/storage"),
    ("settings", "/settings"),
    ("organization-settings", "/settings?tab=organization"),
    ("platform-management", "/management"),
    ("embed-chat", "/embed/chat"),
)


def wait_for_page(page: Page) -> None:
    page.wait_for_load_state("domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=15_000)
    except Exception:
        # Event streams can legitimately keep a connection alive.  The stable
        # DOM wait below is the product-level readiness fallback.
        pass
    page.locator("body").wait_for(state="visible")
    page.wait_for_timeout(500)


def body_summary(page: Page) -> str:
    text = " ".join(page.locator("body").inner_text().split())
    return text[:600]


def capture_route(page: Page, base_url: str, out_dir: Path, name: str, path: str) -> dict[str, Any]:
    response = page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    wait_for_page(page)
    screenshot = out_dir / f"{name}.png"
    page.screenshot(path=str(screenshot), full_page=True)
    return {
        "name": name,
        "requested_path": path,
        "final_url": page.url,
        "http_status": response.status if response is not None else None,
        "title": page.title(),
        "body": body_summary(page),
        "screenshot": str(screenshot),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:9001")
    parser.add_argument(
        "--output-dir",
        default="web/test-results/full-application-acceptance-2026-08-06/reconnaissance",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {
        "base_url": args.base_url,
        "public": [],
        "authenticated": [],
        "console_errors": [],
        "request_failures": [],
        "unexpected_http_errors": [],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        public_context = browser.new_context(viewport={"width": 1440, "height": 1000})
        public_page = public_context.new_page()
        for name, path in PUBLIC_ROUTES:
            result["public"].append(capture_route(public_page, args.base_url, out_dir, name, path))
        public_context.close()

        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.on(
            "console",
            lambda message: result["console_errors"].append(
                {"url": page.url, "type": message.type, "text": message.text}
            )
            if message.type == "error"
            else None,
        )
        page.on(
            "requestfailed",
            lambda request: result["request_failures"].append(
                {
                    "url": request.url,
                    "method": request.method,
                    "failure": request.failure,
                }
            ),
        )
        page.on(
            "response",
            lambda response: result["unexpected_http_errors"].append(
                {
                    "url": response.url,
                    "status": response.status,
                    "method": response.request.method,
                }
            )
            if response.status >= 400
            else None,
        )

        page.goto(f"{args.base_url}/login", wait_until="domcontentloaded")
        wait_for_page(page)
        result["sso_tab_visible_by_default"] = page.locator('[role="tab"]').count() > 0
        page.locator("#login-email").fill("test")
        page.locator("#login-password").fill("test")
        page.locator('button[type="submit"]').click()
        page.wait_for_url("**/chat", timeout=20_000)
        wait_for_page(page)
        result["login_final_url"] = page.url

        for name, path in AUTHENTICATED_ROUTES:
            result["authenticated"].append(
                capture_route(page, args.base_url, out_dir, name, path)
            )

        result["console_errors"] = [
            item
            for item in result["console_errors"]
            if "favicon" not in item["text"].lower()
        ]
        result["unexpected_http_errors"] = [
            item
            for item in result["unexpected_http_errors"]
            if "/favicon" not in item["url"]
        ]
        context.close()
        browser.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
