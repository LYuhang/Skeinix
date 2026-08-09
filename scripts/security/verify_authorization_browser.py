#!/usr/bin/env python3
"""Real-browser authorization and business-capability acceptance gate.

The setup uses the trusted maintenance connection only to add the second test
user to the test business organization; all resource grants, group membership,
organization switching, and user-visible checks go through production APIs and
the real OpenFGA service.  No test-only HTTP route is added to the application.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import re
import time
import uuid

import httpx
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import text


@dataclass(slots=True)
class Identity:
    email: str
    password: str
    user_id: str
    client: httpx.Client


def _csrf(client: httpx.Client) -> str:
    for cookie in client.cookies.jar:
        if cookie.name.endswith("vibecanvas-web-csrf"):
            return cookie.value
    raise AssertionError("browser CSRF cookie was not issued")


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    web_origin: str,
    json: dict | None = None,
    headers: dict[str, str] | None = None,
) -> dict:
    request_headers = {"Origin": web_origin, **(headers or {})}
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and any(
        cookie.name.endswith("vibecanvas-web-session")
        for cookie in client.cookies.jar
    ):
        request_headers["X-CSRF-Token"] = _csrf(client)
    response = client.request(
        method,
        path,
        json=json,
        headers=request_headers,
    )
    if response.status_code >= 400:
        raise AssertionError(
            f"{method} {path} failed: {response.status_code} {response.text}"
        )
    if not response.content:
        return {}
    payload = response.json()
    if not isinstance(payload, dict):
        raise AssertionError(f"{method} {path} returned a non-object payload")
    return payload


def _expect_status(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    web_origin: str,
    expected: int,
    json: dict | None = None,
) -> httpx.Response:
    request_headers = {"Origin": web_origin}
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"} and any(
        cookie.name.endswith("vibecanvas-web-session")
        for cookie in client.cookies.jar
    ):
        request_headers["X-CSRF-Token"] = _csrf(client)
    response = client.request(
        method,
        path,
        json=json,
        headers=request_headers,
    )
    if response.status_code != expected:
        raise AssertionError(
            f"{method} {path}: expected {expected}, got "
            f"{response.status_code} {response.text}"
        )
    return response


def _register(api_url: str, web_origin: str, label: str, suffix: str) -> Identity:
    email = f"browser-authz-{label}-{suffix}@example.com"
    password = "Browser-authz-42!"
    client = httpx.Client(base_url=api_url, timeout=15)
    payload = _request(
        client,
        "POST",
        "/api/v1/auth/register",
        web_origin=web_origin,
        json={"email": email, "username": label, "password": password},
    )
    user = payload.get("user")
    if not isinstance(user, dict) or not user.get("user_id"):
        raise AssertionError("registration did not return a user identity")
    return Identity(email, password, str(user["user_id"]), client)


async def _seed_business_membership(
    *,
    organization_id: str,
    user_id: str,
    invited_by: str,
    role: str = "member",
) -> None:
    from vibecanvas_api.authorization.openfga_client import (
        openfga_client_from_config,
    )
    from vibecanvas_api.authorization.projection import reconcile_organization
    from vibecanvas_api.storage.sync_session import short_admin_connection

    async with short_admin_connection() as connection:
        async with connection.begin():
            await connection.execute(
                text(
                    """
                    INSERT INTO org_memberships (
                        membership_id, user_id, tenant_id, org_role, status,
                        invited_by
                    ) VALUES (
                        :membership_id, :user_id, :tenant_id, :role,
                        'active', :invited_by
                    )
                    ON CONFLICT (user_id, tenant_id) DO UPDATE SET
                        org_role = EXCLUDED.org_role,
                        status = EXCLUDED.status,
                        invited_by = EXCLUDED.invited_by,
                        updated_at = now()
                    """
                ),
                {
                    "membership_id": uuid.uuid4(),
                    "user_id": uuid.UUID(user_id),
                    "tenant_id": uuid.UUID(organization_id),
                    "invited_by": uuid.UUID(invited_by),
                    "role": role,
                },
            )

    client = openfga_client_from_config()
    try:
        stats = await reconcile_organization(client, organization_id)
        if stats.failures:
            raise AssertionError("organization membership projection failed")
    finally:
        await client.close()


async def _seed_business_memberships(
    *,
    organization_id: str,
    invited_by: str,
    memberships: tuple[tuple[str, str], ...],
) -> None:
    """Seed the verifier roles inside one event loop.

    The application's async SQLAlchemy engines are process-global.  Repeated
    ``asyncio.run`` calls would bind their pool to different loops and make the
    second role fail before browser acceptance starts.
    """
    for user_id, role in memberships:
        await _seed_business_membership(
            organization_id=organization_id,
            user_id=user_id,
            invited_by=invited_by,
            role=role,
        )


def _switch(client: httpx.Client, organization_id: str, web_origin: str) -> None:
    _request(
        client,
        "POST",
        "/api/v1/organizations/active",
        web_origin=web_origin,
        json={"organization_id": organization_id},
    )


def _create_workflow(
    client: httpx.Client,
    *,
    name: str,
    web_origin: str,
) -> str:
    payload = _request(
        client,
        "POST",
        "/api/v1/workflows",
        web_origin=web_origin,
        json={"name": name, "description": "authorization browser gate", "tags": []},
    )
    return str(payload["wf_id"])


def _change_binding(
    client: httpx.Client,
    *,
    workflow_id: str,
    relation: str,
    subject_type: str,
    subject_id: str,
    subject_relation: str | None,
    present: bool,
    web_origin: str,
) -> None:
    _request(
        client,
        "POST" if present else "DELETE",
        f"/api/v1/workflows/{workflow_id}/access",
        web_origin=web_origin,
        json={
            "relation": relation,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "subject_relation": subject_relation,
        },
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )


def _login(page: Page, web_origin: str, identity: Identity) -> None:
    page.goto(f"{web_origin}/login")
    page.wait_for_load_state("networkidle")
    page.locator("#login-email").fill(identity.email)
    page.locator("#login-password").fill(identity.password)
    page.locator('button[type="submit"]').click()
    page.wait_for_url("**/chat")


def _switch_browser_organization(
    page: Page,
    *,
    organization_name: str,
) -> None:
    page.locator('[data-testid="organization-switcher"]').click()
    with page.expect_response(
        lambda response: response.url.endswith("/api/v1/organizations/active")
        and response.request.method == "POST"
    ):
        page.get_by_role("menuitem").filter(has_text=organization_name).click()
    expect(page.locator('[data-testid="organization-switcher"]')).to_contain_text(
        organization_name
    )


def _workflow_row(page: Page, name: str):
    return page.locator('[data-testid="wf-row"]').filter(has_text=name)


def _open_row_menu(page: Page, name: str) -> None:
    _workflow_row(page, name).locator('[data-testid="wf-row-menu"]').click()


def _assert_role_ui(
    page: Page,
    *,
    web_origin: str,
    workflow_id: str,
    workflow_name: str,
    role: str,
) -> None:
    page.goto(f"{web_origin}/workspace")
    page.wait_for_load_state("networkidle")
    expect(_workflow_row(page, workflow_name)).to_have_count(1)
    _open_row_menu(page, workflow_name)

    expect(page.locator('[data-testid="wf-row-duplicate"]')).to_be_enabled()
    expected = {
        "viewer": set(),
        "editor": {"wf-row-edit"},
        "operator": set(),
        "manager": {
            "wf-row-edit",
            "wf-row-share",
            "wf-row-deploy",
            "wf-row-delete",
        },
    }[role]
    for test_id in (
        "wf-row-edit",
        "wf-row-share",
        "wf-row-deploy",
        "wf-row-delete",
    ):
        expect(page.locator(f'[data-testid="{test_id}"]')).to_have_count(
            1 if test_id in expected else 0
        )
    page.keyboard.press("Escape")

    page.goto(f"{web_origin}/workflow/{workflow_id}")
    page.wait_for_load_state("networkidle")
    expect(page.locator('[data-action="execute"]')).to_be_enabled(
        enabled=role in {"operator", "manager"}
    )
    expect(page.locator('[data-action="canvas-new-version"]')).to_be_enabled(
        enabled=role in {"editor", "manager"}
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-origin", default="http://127.0.0.1:9001")
    args = parser.parse_args()
    suffix = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    organization_name = f"Authorization Browser {suffix}"
    personal_workflow = f"Personal stale sentinel {suffix}"
    business_workflow = f"Role matrix workflow {suffix}"
    group_name = f"Inherited viewers {suffix}"

    owner = _register(args.api_url, args.web_origin, "owner", suffix)
    member = _register(args.api_url, args.web_origin, "member", suffix)
    guest = _register(args.api_url, args.web_origin, "guest", suffix)
    auditor = _register(args.api_url, args.web_origin, "auditor", suffix)
    try:
        personal_workflow_id = _create_workflow(
            member.client,
            name=personal_workflow,
            web_origin=args.web_origin,
        )
        organization = _request(
            owner.client,
            "POST",
            "/api/v1/organizations",
            web_origin=args.web_origin,
            json={
                "name": organization_name,
                "slug": f"authz-browser-{suffix}".lower(),
            },
        )
        organization_id = str(organization["organization_id"])
        _switch(owner.client, organization_id, args.web_origin)
        asyncio.run(
            _seed_business_memberships(
                organization_id=organization_id,
                invited_by=owner.user_id,
                memberships=(
                    (member.user_id, "member"),
                    (guest.user_id, "guest"),
                    (auditor.user_id, "auditor"),
                ),
            )
        )
        _switch(guest.client, organization_id, args.web_origin)
        _switch(auditor.client, organization_id, args.web_origin)
        business_workflow_id = _create_workflow(
            owner.client,
            name=business_workflow,
            web_origin=args.web_origin,
        )

        _expect_status(
            guest.client,
            "POST",
            "/api/v1/workflows",
            web_origin=args.web_origin,
            expected=404,
            json={"name": "Guest must not create"},
        )
        guest_unshared = _request(
            guest.client,
            "GET",
            "/api/v1/workflows",
            web_origin=args.web_origin,
        )
        assert not any(
            item.get("wf_id") == business_workflow_id
            for item in guest_unshared.get("items", [])
        ), "guest browsed an unshared organization workflow"

        auditor_inventory = _request(
            auditor.client,
            "GET",
            "/api/v1/workflows",
            web_origin=args.web_origin,
        )
        auditor_item = next(
            (
                item
                for item in auditor_inventory.get("items", [])
                if item.get("wf_id") == business_workflow_id
            ),
            None,
        )
        assert auditor_item is not None, "auditor cannot review workflow metadata"
        auditor_capabilities = set(
            auditor_item.get("access", {}).get("capabilities", [])
        )
        assert "view_metadata" in auditor_capabilities
        assert not auditor_capabilities.intersection(
            {"view", "export", "update", "execute", "manage_access", "delete"}
        )
        assert auditor_item.get("description") == ""
        assert auditor_item.get("tags") == []
        _expect_status(
            auditor.client,
            "GET",
            f"/api/v1/workflows/{business_workflow_id}",
            web_origin=args.web_origin,
            expected=404,
        )
        audit_page = _request(
            auditor.client,
            "GET",
            "/api/v1/audit?limit=50",
            web_origin=args.web_origin,
        )
        assert audit_page.get("items"), "auditor audit feed is empty"
        group = _request(
            owner.client,
            "POST",
            f"/api/v1/organizations/{organization_id}/groups",
            web_origin=args.web_origin,
            json={"name": group_name, "kind": "team"},
        )
        group_id = str(group["group_id"])
        _request(
            owner.client,
            "PUT",
            f"/api/v1/organizations/{organization_id}/groups/{group_id}/members/{member.user_id}",
            web_origin=args.web_origin,
            json={"role": "member", "status": "active"},
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            _login(page, args.web_origin, member)
            page.goto(f"{args.web_origin}/workspace")
            page.wait_for_load_state("networkidle")
            expect(_workflow_row(page, personal_workflow)).to_have_count(1)

            page.evaluate(
                r"""
                () => {
                  window.__authzSamples = [];
                  window.__authzSampling = true;
                  const sample = () => {
                    if (!window.__authzSampling) return;
                    window.__authzSamples.push({
                      at: performance.now(),
                      organization: document.querySelector(
                        '[data-testid="organization-switcher"]'
                      )?.textContent || '',
                      text: Array.from(document.querySelectorAll('[data-testid="wf-row"]'))
                        .map((node) => node.textContent || '').join('\n'),
                    });
                    requestAnimationFrame(sample);
                  };
                  requestAnimationFrame(sample);
                }
                """
            )
            page.locator('[data-testid="organization-switcher"]').click()
            with page.expect_response(
                lambda response: response.url.endswith("/api/v1/organizations/active")
                and response.request.method == "POST"
            ):
                page.get_by_role("menuitem").filter(
                    has_text=organization_name
                ).click()
            expect(page.locator('[data-testid="organization-switcher"]')).to_contain_text(
                organization_name
            )
            expect(_workflow_row(page, personal_workflow)).to_have_count(0)
            samples = page.evaluate(
                """
                () => {
                  window.__authzSampling = false;
                  return window.__authzSamples;
                }
                """
            )
            new_organization_samples = [
                sample
                for sample in samples
                if organization_name in sample["organization"]
            ]
            assert new_organization_samples, "new-organization frame was not sampled"
            assert all(
                personal_workflow not in sample["text"]
                for sample in new_organization_samples
            ), "old-organization workflow flashed under the new organization label"
            expect(_workflow_row(page, business_workflow)).to_have_count(0)

            guest_context = browser.new_context()
            guest_page = guest_context.new_page()
            _login(guest_page, args.web_origin, guest)
            _switch_browser_organization(
                guest_page,
                organization_name=organization_name,
            )
            guest_page.goto(f"{args.web_origin}/workspace")
            guest_page.wait_for_load_state("networkidle")
            expect(_workflow_row(guest_page, business_workflow)).to_have_count(0)

            auditor_context = browser.new_context()
            auditor_page = auditor_context.new_page()
            _login(auditor_page, args.web_origin, auditor)
            _switch_browser_organization(
                auditor_page,
                organization_name=organization_name,
            )
            auditor_page.goto(f"{args.web_origin}/workspace")
            auditor_page.wait_for_load_state("networkidle")
            auditor_row = _workflow_row(auditor_page, business_workflow)
            expect(auditor_row).to_have_count(1)
            expect(
                auditor_row.locator('[data-testid="wf-row-metadata-only"]')
            ).to_have_count(1)
            expect(
                auditor_row.locator('[data-testid="wf-row-open"]')
            ).to_be_disabled()
            expect(
                auditor_row.locator(
                    f'a[href="/workflow/{business_workflow_id}"]'
                )
            ).to_have_count(0)
            _open_row_menu(auditor_page, business_workflow)
            expect(
                auditor_page.locator('[data-testid="wf-row-duplicate"]')
            ).to_be_disabled()
            for test_id in (
                "wf-row-edit",
                "wf-row-share",
                "wf-row-deploy",
                "wf-row-delete",
            ):
                expect(
                    auditor_page.locator(f'[data-testid="{test_id}"]')
                ).to_have_count(0)
            auditor_page.keyboard.press("Escape")

            auditor_page.goto(f"{args.web_origin}/settings?tab=organization")
            auditor_page.wait_for_load_state("networkidle")
            expect(
                auditor_page.locator(
                    '[data-testid="organization-audit-section"]'
                )
            ).to_be_visible()
            expect(
                auditor_page.locator('[data-testid="organization-audit-row"]').first
            ).to_be_visible()
            expect(
                auditor_page.get_by_role("button", name="New group")
            ).to_have_count(0)
            expect(
                auditor_page.get_by_role("button", name="Rotate")
            ).to_have_count(0)

            _change_binding(
                owner.client,
                workflow_id=business_workflow_id,
                relation="viewer",
                subject_type="user",
                subject_id=guest.user_id,
                subject_relation=None,
                present=True,
                web_origin=args.web_origin,
            )
            guest_page.reload()
            guest_page.wait_for_load_state("networkidle")
            guest_row = _workflow_row(guest_page, business_workflow)
            expect(guest_row).to_have_count(1)
            expect(
                guest_row.locator('[data-testid="wf-row-open"]')
            ).to_be_enabled()
            expect(
                guest_row.locator(
                    f'a[href="/workflow/{business_workflow_id}"]'
                )
            ).to_have_count(2)
            _expect_status(
                guest.client,
                "GET",
                f"/api/v1/workflows/{business_workflow_id}",
                web_origin=args.web_origin,
                expected=200,
            )

            current_role: str | None = None
            for role in ("viewer", "editor", "operator", "manager"):
                if current_role is not None:
                    _change_binding(
                        owner.client,
                        workflow_id=business_workflow_id,
                        relation=current_role,
                        subject_type="user",
                        subject_id=member.user_id,
                        subject_relation=None,
                        present=False,
                        web_origin=args.web_origin,
                    )
                _change_binding(
                    owner.client,
                    workflow_id=business_workflow_id,
                    relation=role,
                    subject_type="user",
                    subject_id=member.user_id,
                    subject_relation=None,
                    present=True,
                    web_origin=args.web_origin,
                )
                current_role = role
                _assert_role_ui(
                    page,
                    web_origin=args.web_origin,
                    workflow_id=business_workflow_id,
                    workflow_name=business_workflow,
                    role=role,
                )

            second_page = context.new_page()
            second_page.goto(f"{args.web_origin}/workspace")
            second_page.wait_for_load_state("networkidle")
            expect(_workflow_row(second_page, business_workflow)).to_have_count(1)
            assert current_role is not None
            _change_binding(
                owner.client,
                workflow_id=business_workflow_id,
                relation=current_role,
                subject_type="user",
                subject_id=member.user_id,
                subject_relation=None,
                present=False,
                web_origin=args.web_origin,
            )
            for tab in (page, second_page):
                tab.goto(f"{args.web_origin}/workspace")
                tab.wait_for_load_state("networkidle")
                expect(_workflow_row(tab, business_workflow)).to_have_count(0)

            _change_binding(
                owner.client,
                workflow_id=business_workflow_id,
                relation="viewer",
                subject_type="group",
                subject_id=group_id,
                subject_relation="member",
                present=True,
                web_origin=args.web_origin,
            )
            for tab in (page, second_page):
                tab.reload()
                tab.wait_for_load_state("networkidle")
                expect(_workflow_row(tab, business_workflow)).to_have_count(1)

            owner_context = browser.new_context()
            owner_page = owner_context.new_page()
            _login(owner_page, args.web_origin, owner)
            owner_page.goto(f"{args.web_origin}/workspace")
            owner_page.wait_for_load_state("networkidle")
            owner_page.locator('[data-testid="organization-switcher"]').click()
            owner_page.get_by_role("menuitem").filter(
                has_text=organization_name
            ).click()
            expect(_workflow_row(owner_page, business_workflow)).to_have_count(1)
            _open_row_menu(owner_page, business_workflow)
            owner_page.locator('[data-testid="wf-row-share"]').click()
            dialog = owner_page.get_by_role("dialog")
            expect(dialog).to_contain_text(group_name)
            expect(dialog).to_contain_text("group · viewer · direct")
            expect(dialog).to_contain_text(
                re.compile(r"Inherited access is read-only|继承权限")
            )

            _change_binding(
                owner.client,
                workflow_id=business_workflow_id,
                relation="viewer",
                subject_type="group",
                subject_id=group_id,
                subject_relation="member",
                present=False,
                web_origin=args.web_origin,
            )
            for tab in (page, second_page):
                tab.reload()
                tab.wait_for_load_state("networkidle")
                expect(_workflow_row(tab, business_workflow)).to_have_count(0)

            # The guest's independent direct share survives the group revoke,
            # then disappears immediately when revoked at its actual source.
            guest_page.reload()
            guest_page.wait_for_load_state("networkidle")
            expect(_workflow_row(guest_page, business_workflow)).to_have_count(1)
            _change_binding(
                owner.client,
                workflow_id=business_workflow_id,
                relation="viewer",
                subject_type="user",
                subject_id=guest.user_id,
                subject_relation=None,
                present=False,
                web_origin=args.web_origin,
            )
            guest_page.reload()
            guest_page.wait_for_load_state("networkidle")
            expect(_workflow_row(guest_page, business_workflow)).to_have_count(0)
            _expect_status(
                guest.client,
                "GET",
                f"/api/v1/workflows/{business_workflow_id}",
                web_origin=args.web_origin,
                expected=404,
            )

            owner_context.close()
            auditor_context.close()
            guest_context.close()
            context.close()
            browser.close()

        # Keep the fixture's two resource roots explicit in the success output
        # without exposing any credential or Session material.
        print(
            "authorization_browser_gate=pass "
            "roles=4 guest=explicit-share auditor=metadata-audit "
            f"multitab=2 personal_workflow={personal_workflow_id}"
        )
        return 0
    finally:
        owner.client.close()
        member.client.close()
        guest.client.close()
        auditor.client.close()


if __name__ == "__main__":
    raise SystemExit(main())
