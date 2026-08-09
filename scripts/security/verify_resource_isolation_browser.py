#!/usr/bin/env python3
"""Live same-organization isolation and localized denial browser gate."""
from __future__ import annotations

import argparse
import asyncio
import time
import uuid

from playwright.sync_api import expect, sync_playwright

from verify_authorization_browser import (
    _create_workflow,
    _csrf,
    _login,
    _register,
    _request,
    _seed_business_membership,
    _switch,
    _workflow_row,
)


def _assert_denied(response, operation: str, *private_markers: str) -> None:
    if response.status_code not in {403, 404}:
        raise AssertionError(
            f"{operation} was not denied: {response.status_code} {response.text}"
        )
    body = response.text
    for marker in private_markers:
        if marker and marker in body:
            raise AssertionError(f"{operation} leaked a private marker")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-origin", default="http://127.0.0.1:9001")
    args = parser.parse_args()
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    organization_name = f"Isolation Browser {suffix}"
    workflow_name = f"Private workflow sentinel {suffix}"
    file_name = f"private-preview-{suffix}.txt"
    file_content = f"private-content-sentinel-{suffix}".encode()

    owner = _register(args.api_url, args.web_origin, "isolation-owner", suffix)
    intruder = _register(
        args.api_url,
        args.web_origin,
        "isolation-intruder",
        suffix,
    )
    try:
        organization = _request(
            owner.client,
            "POST",
            "/api/v1/organizations",
            web_origin=args.web_origin,
            json={
                "name": organization_name,
                "slug": f"isolation-browser-{suffix}".lower(),
            },
        )
        organization_id = str(organization["organization_id"])
        _switch(owner.client, organization_id, args.web_origin)
        asyncio.run(
            _seed_business_membership(
                organization_id=organization_id,
                user_id=intruder.user_id,
                invited_by=owner.user_id,
            )
        )
        _switch(intruder.client, organization_id, args.web_origin)
        workflow_id = _create_workflow(
            owner.client,
            name=workflow_name,
            web_origin=args.web_origin,
        )

        bootstrap = _request(
            owner.client,
            "GET",
            "/api/v1/chats/bootstrap?surface=chat",
            web_origin=args.web_origin,
        )
        carrier_scope_id = str(bootstrap["carrier_scope_id"])
        chat_id = f"isolation_chat_{uuid.uuid4().hex[:16]}"
        upload = owner.client.post(
            f"/api/v1/chat-scopes/{carrier_scope_id}/chats/{chat_id}/attachments",
            params={"attachment_type": "file"},
            headers={
                "Origin": args.web_origin,
                "X-CSRF-Token": _csrf(owner.client),
            },
            files={"file": (file_name, file_content, "text/plain")},
        )
        if upload.status_code >= 400:
            raise AssertionError(
                f"chat fixture upload failed: {upload.status_code} {upload.text}"
            )
        path = str(upload.json()["path"])
        workspace = _request(
            owner.client,
            "GET",
            f"/api/v1/chats/workspace?chat_id={chat_id}",
            web_origin=args.web_origin,
        )
        workspace_scope_id = str(workspace["workspace_scope_id"])
        file_ref = {
            "schemaVersion": 1,
            "scope": "chat",
            "chatId": chat_id,
            "path": path,
        }

        marker_args = (workflow_name, file_name, file_content.decode())
        client = intruder.client
        common_headers = {"Origin": args.web_origin}
        mutation_headers = {
            **common_headers,
            "X-CSRF-Token": _csrf(client),
        }
        denial_requests = [
            (
                "workflow detail",
                client.get(
                    f"/api/v1/workflows/{workflow_id}",
                    headers=common_headers,
                ),
            ),
            (
                "chat runtime",
                client.get(
                    f"/api/v1/chats/{chat_id}/runtime",
                    headers=common_headers,
                ),
            ),
            (
                "chat history",
                client.get(
                    f"/api/v1/chat-scopes/{carrier_scope_id}/chats/{chat_id}/messages",
                    headers=common_headers,
                ),
            ),
            (
                "background jobs",
                client.get(
                    f"/api/v1/chat-scopes/{carrier_scope_id}/chats/{chat_id}/background-jobs",
                    headers=common_headers,
                ),
            ),
            (
                "background event stream",
                client.get(
                    f"/api/v1/chat-scopes/{carrier_scope_id}/chats/{chat_id}/background-jobs/events",
                    headers={**common_headers, "Accept": "text/event-stream"},
                ),
            ),
            (
                "sandbox status",
                client.get(
                    "/api/v1/chats/sandbox",
                    params={"chat_id": chat_id},
                    headers=common_headers,
                ),
            ),
            (
                "VFS content",
                client.get(
                    "/api/v1/vfs/content",
                    params={"wf_id": workspace_scope_id, "path": path},
                    headers=common_headers,
                ),
            ),
            (
                "Debug VFS listing",
                client.get(
                    "/api/v1/vfs",
                    params={
                        "wf_id": workspace_scope_id,
                        "prefix": "/logs/.debug/",
                        "include_hidden": "true",
                    },
                    headers=common_headers,
                ),
            ),
            (
                "Preview descriptor",
                client.post(
                    "/api/v1/previews/resolve",
                    headers=mutation_headers,
                    json={"fileRef": file_ref},
                ),
            ),
            (
                "Preview event stream",
                client.get(
                    "/api/v1/previews/events",
                    params={"scope": "chat", "chat_id": chat_id, "path": path},
                    headers={**common_headers, "Accept": "text/event-stream"},
                ),
            ),
        ]
        for operation, response in denial_requests:
            _assert_denied(response, operation, *marker_args)

        workflows = _request(
            client,
            "GET",
            "/api/v1/workflows",
            web_origin=args.web_origin,
        )
        assert workflow_id not in str(workflows)
        assert workflow_name not in str(workflows)
        sandboxes = _request(
            client,
            "GET",
            f"/api/v1/chats/sandboxes?chat_id={chat_id}",
            web_origin=args.web_origin,
        )
        assert sandboxes.get("items") == []

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            _login(page, args.web_origin, intruder)
            page.locator('[data-testid="organization-switcher"]').click()
            page.get_by_role("menuitem").filter(
                has_text=organization_name
            ).click()
            expect(page.locator('[data-testid="organization-switcher"]')).to_contain_text(
                organization_name
            )

            page.goto(f"{args.web_origin}/workspace")
            page.wait_for_load_state("networkidle")
            expect(_workflow_row(page, workflow_name)).to_have_count(0)
            expect(page.locator(f'[data-chat-id="{chat_id}"]')).to_have_count(0)

            page.evaluate(
                "() => localStorage.setItem('vibecanvas.locale', 'en')"
            )
            page.goto(f"{args.web_origin}/workflow/{workflow_id}")
            alert = page.get_by_role("alert")
            expect(alert).to_contain_text("Workflow unavailable", timeout=30_000)
            expect(alert).to_contain_text(
                "This workflow does not exist or you no longer have permission to view it."
            )
            expect(alert).not_to_contain_text("resource_not_found")
            expect(page.locator("body")).not_to_contain_text(workflow_name)

            page.evaluate(
                "() => localStorage.setItem('vibecanvas.locale', 'zh')"
            )
            page.reload()
            alert = page.get_by_role("alert")
            expect(alert).to_contain_text("工作流不可用", timeout=30_000)
            expect(alert).to_contain_text("该工作流不存在，或你已没有查看权限。")
            expect(alert).not_to_contain_text("resource_not_found")
            expect(page.locator("body")).not_to_contain_text(workflow_name)
            context.close()
            browser.close()

        print(
            "resource_isolation_browser_gate=pass "
            f"denials={len(denial_requests)} locales=2 lists=3"
        )
        return 0
    finally:
        owner.client.close()
        intruder.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
