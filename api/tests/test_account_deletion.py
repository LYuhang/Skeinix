from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text


async def _register(client, email: str) -> tuple[str, str]:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": "Delete Me", "password": "pw12345678"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["session_token"], r.json()["user"]["user_id"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_delete_account_requires_matching_email(client):
    email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    token, _ = await _register(client, email)

    r = await client.post(
        "/api/v1/auth/delete-account",
        json={"email": "other@example.com"},
        headers=_hdr(token),
    )
    assert r.status_code == 400, r.text

    me = await client.get("/api/v1/auth/me", headers=_hdr(token))
    assert me.status_code == 200, me.text


@pytest.mark.asyncio
async def test_delete_account_blocks_last_owner_of_business_organization(client):
    email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    token, _ = await _register(client, email)
    organization_name = f"Deletion Guard {uuid.uuid4().hex[:8]}"

    created = await client.post(
        "/api/v1/organizations",
        json={
            "name": organization_name,
            "slug": f"deletion-guard-{uuid.uuid4().hex[:8]}",
        },
        headers=_hdr(token),
    )
    assert created.status_code == 201, created.text

    deleted = await client.post(
        "/api/v1/auth/delete-account",
        json={"email": email},
        headers=_hdr(token),
    )
    assert deleted.status_code == 409, deleted.text
    assert organization_name in deleted.json()["detail"]

    me = await client.get("/api/v1/auth/me", headers=_hdr(token))
    assert me.status_code == 200, me.text


@pytest.mark.asyncio
async def test_delete_account_allows_business_organization_with_another_owner(
    client,
    pg_engine,
):
    owner_email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    owner_token, _ = await _register(client, owner_email)
    _other_token, other_user_id = await _register(
        client,
        f"owner_{uuid.uuid4().hex[:12]}@example.com",
    )
    created = await client.post(
        "/api/v1/organizations",
        json={
            "name": f"Shared Organization {uuid.uuid4().hex[:8]}",
            "slug": f"shared-org-{uuid.uuid4().hex[:8]}",
        },
        headers=_hdr(owner_token),
    )
    assert created.status_code == 201, created.text
    organization_id = created.json()["organization_id"]

    async with pg_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO org_memberships("
                "membership_id, user_id, tenant_id, org_role, status"
                ") VALUES ("
                "gen_random_uuid(), :user_id, :tenant_id, 'owner', 'active'"
                ")"
            ),
            {"user_id": other_user_id, "tenant_id": organization_id},
        )

    deleted = await client.post(
        "/api/v1/auth/delete-account",
        json={"email": owner_email},
        headers=_hdr(owner_token),
    )
    assert deleted.status_code == 204, deleted.text


@pytest.mark.asyncio
async def test_delete_account_marks_pending_and_invalidates_session(
    client,
    pg_engine,
):
    email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    token, user_id = await _register(client, email)

    wf = await client.post(
        "/api/v1/workflows",
        json={"name": "delete-me"},
        headers=_hdr(token),
    )
    assert wf.status_code == 201, wf.text
    workflow_id = wf.json()["wf_id"]

    r = await client.post(
        "/api/v1/auth/delete-account",
        json={"email": email},
        headers=_hdr(token),
    )
    assert r.status_code == 204, r.text

    me = await client.get("/api/v1/auth/me", headers=_hdr(token))
    assert me.status_code == 401, me.text

    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                """
                SELECT
                  (SELECT status FROM users WHERE user_id = :user_id) AS user_status,
                  (SELECT count(*) FROM account_deletion_requests
                    WHERE user_id = :user_id AND status = 'pending') AS deletion_requests,
                  (SELECT count(*) FROM data_purge_jobs p
                    JOIN account_deletion_requests d ON d.id = p.deletion_request_id
                    WHERE d.user_id = :user_id AND p.status = 'queued') AS purge_jobs,
                  (SELECT count(*) FROM auth_identities
                    WHERE user_id = :user_id) AS identities,
                  (SELECT count(*) FROM sessions WHERE token_hash IS NOT NULL) AS sessions,
                  (SELECT count(*) FROM workflows WHERE wf_id = :workflow_id) AS workflows,
                  (SELECT count(*) FROM tenants WHERE name = :email) AS tenants,
                  (SELECT email_snapshot FROM account_deletion_requests
                    WHERE user_id=:user_id LIMIT 1) AS email_snapshot,
                  (SELECT email_snapshot_ciphertext FROM account_deletion_requests
                    WHERE user_id=:user_id LIMIT 1) AS email_ciphertext,
                  (SELECT deletion_mode FROM account_deletion_requests
                    WHERE user_id=:user_id LIMIT 1) AS deletion_mode,
                  (SELECT purge_after FROM account_deletion_requests
                    WHERE user_id=:user_id LIMIT 1) AS purge_after
                """
            ),
            {"email": email, "user_id": user_id, "workflow_id": workflow_id},
        )).one()
    assert row.user_status == "pending_deletion"
    assert row.deletion_requests == 1
    assert row.purge_jobs == 1
    assert row.identities == 1
    assert row.sessions == 0
    assert row.workflows == 1
    assert row.tenants == 0
    assert row.email_snapshot == ""
    assert email not in row.email_ciphertext
    assert row.deletion_mode == "immediate"
    assert row.purge_after <= datetime.now(timezone.utc)

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw12345678"},
    )
    assert login.status_code == 423, login.text


@pytest.mark.asyncio
async def test_immediate_delete_account_cannot_be_cancelled(client):
    email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    token, _user_id = await _register(client, email)
    r = await client.post(
        "/api/v1/auth/delete-account",
        json={"email": email},
        headers=_hdr(token),
    )
    assert r.status_code == 204, r.text

    cancel = await client.post(
        "/api/v1/auth/cancel-delete-account",
        json={"email": email, "password": "pw12345678"},
    )
    assert cancel.status_code == 409, cancel.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw12345678"},
    )
    assert login.status_code == 423, login.text


@pytest.mark.asyncio
async def test_delayed_delete_account_can_be_cancelled(
    client,
    pg_engine,
    monkeypatch,
):
    from vibecanvas_api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.app_config, "account_deletion_mode", "delayed")
    monkeypatch.setattr(auth_routes.app_config, "account_deletion_retention_days", 14)

    email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    token, user_id = await _register(client, email)
    enabled_mcp_id = uuid.uuid4()
    disabled_mcp_id = uuid.uuid4()
    async with pg_engine.begin() as connection:
        tenant_id = (
            await connection.execute(
                text("SELECT tenant_id FROM users WHERE user_id=:user_id"),
                {"user_id": user_id},
            )
        ).scalar_one()
        await connection.execute(
            text(
                "INSERT INTO mcp_servers("
                "id, tenant_id, user_id, name, tool_prefix, transport, "
                "endpoint, enabled"
                ") VALUES "
                "(:enabled_id, :tenant_id, :user_id, 'Enabled server', "
                "'enabled_server', 'sse', 'https://example.com/enabled', true),"
                "(:disabled_id, :tenant_id, :user_id, 'Disabled server', "
                "'disabled_server', 'sse', 'https://example.com/disabled', false)"
            ),
            {
                "enabled_id": enabled_mcp_id,
                "disabled_id": disabled_mcp_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
        )
    r = await client.post(
        "/api/v1/auth/delete-account",
        json={"email": email},
        headers=_hdr(token),
    )
    assert r.status_code == 204, r.text

    async with pg_engine.connect() as connection:
        frozen_rows = dict(
            (
                await connection.execute(
                    text(
                        "SELECT id, enabled FROM mcp_servers "
                        "WHERE id IN (:enabled_id, :disabled_id)"
                    ),
                    {
                        "enabled_id": enabled_mcp_id,
                        "disabled_id": disabled_mcp_id,
                    },
                )
            ).all()
        )
    assert frozen_rows == {
        enabled_mcp_id: False,
        disabled_mcp_id: False,
    }

    cancel = await client.post(
        "/api/v1/auth/cancel-delete-account",
        json={"email": email, "password": "pw12345678"},
    )
    assert cancel.status_code == 200, cancel.text

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw12345678"},
    )
    assert login.status_code == 200, login.text

    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                """
                SELECT
                  (SELECT status FROM users WHERE user_id = :user_id) AS user_status,
                  (SELECT status FROM account_deletion_requests
                    WHERE user_id = :user_id ORDER BY requested_at DESC LIMIT 1) AS request_status,
                  (SELECT deletion_mode FROM account_deletion_requests
                    WHERE user_id = :user_id ORDER BY requested_at DESC LIMIT 1) AS deletion_mode,
                  (SELECT p.status FROM data_purge_jobs p
                    JOIN account_deletion_requests d ON d.id = p.deletion_request_id
                    WHERE d.user_id = :user_id ORDER BY p.created_at DESC LIMIT 1) AS purge_status,
                  (SELECT enabled FROM mcp_servers
                    WHERE id=:enabled_mcp_id) AS enabled_mcp,
                  (SELECT enabled FROM mcp_servers
                    WHERE id=:disabled_mcp_id) AS disabled_mcp
                """
            ),
            {
                "email": email,
                "user_id": user_id,
                "enabled_mcp_id": enabled_mcp_id,
                "disabled_mcp_id": disabled_mcp_id,
            },
        )).one()
    assert row.user_status == "active"
    assert row.request_status == "cancelled"
    assert row.deletion_mode == "delayed"
    assert row.purge_status == "cancelled"
    assert row.enabled_mcp is True
    assert row.disabled_mcp is False
