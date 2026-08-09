from __future__ import annotations

import uuid

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
                    WHERE user_id=:user_id LIMIT 1) AS email_ciphertext
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

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "pw12345678"},
    )
    assert login.status_code == 423, login.text


@pytest.mark.asyncio
async def test_cancel_delete_account_reactivates_login(client, pg_engine):
    email = f"del_{uuid.uuid4().hex[:12]}@example.com"
    token, user_id = await _register(client, email)
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
                  (SELECT p.status FROM data_purge_jobs p
                    JOIN account_deletion_requests d ON d.id = p.deletion_request_id
                    WHERE d.user_id = :user_id ORDER BY p.created_at DESC LIMIT 1) AS purge_status
                """
            ),
            {"email": email, "user_id": user_id},
        )).one()
    assert row.user_status == "active"
    assert row.request_status == "cancelled"
    assert row.purge_status == "cancelled"
