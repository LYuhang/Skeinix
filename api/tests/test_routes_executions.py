"""Executions router smoke. Full SSE streaming gate lives in T17.

Auth: the legacy ``VIBECANVAS_API_DEV_TOKEN`` + sync ``TestClient`` +
``Bearer tok`` harness is DEAD (dev-token auth was removed from the app).
These route-contract tests now use the conftest async ``client`` fixture +
a real ``register → session_token`` (the same pattern as
``test_routes_vfs.py``). The 404 / empty-list contracts only need auth to
work — no execution is driven here.
"""

from __future__ import annotations

import uuid

import pytest


async def _register(client) -> str:
    """Register a fresh user, return its bearer session token. Email is
    unique per call (uuid) so it never collides with another test's row
    (the conftest truncates between tests, but within a test we may
    register several users)."""
    email = f"exec_{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": "Test User", "password": "pw12345678"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["session_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_execution_status_404_for_unknown(client, pg_engine):
    tok = await _register(client)
    r = await client.get("/api/v1/executions/unknown", headers=_hdr(tok))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_cancel_404_for_unknown(client, pg_engine):
    tok = await _register(client)
    r = await client.post("/api/v1/executions/unknown/cancel", headers=_hdr(tok))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_executions_empty(client, pg_engine):
    tok = await _register(client)
    r = await client.post("/api/v1/workflows", json={"name": "wf"},
                          headers=_hdr(tok))
    assert r.status_code == 201, r.text
    wf_id = r.json()["wf_id"]
    r = await client.get(f"/api/v1/workflows/{wf_id}/executions",
                         headers=_hdr(tok))
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == []
    assert body["total"] == 0


@pytest.mark.asyncio
async def test_workflow_execution_status_is_null_before_first_run(client, pg_engine):
    tok = await _register(client)
    created = await client.post(
        "/api/v1/workflows", json={"name": "fresh"}, headers=_hdr(tok),
    )
    assert created.status_code == 201, created.text

    response = await client.get(
        f"/api/v1/workflows/{created.json()['wf_id']}/execution/status",
        headers=_hdr(tok),
    )

    assert response.status_code == 200, response.text
    assert response.json() is None


@pytest.mark.asyncio
async def test_workflow_execution_status_404_for_unknown_workflow(client, pg_engine):
    tok = await _register(client)
    response = await client.get(
        "/api/v1/workflows/no_such_wf/execution/status", headers=_hdr(tok),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_start_execution_404_for_unknown_wf(client, pg_engine):
    tok = await _register(client)
    r = await client.post(
        "/api/v1/workflows/no_such_wf/executions",
        json={"mode": "single", "input": {}}, headers=_hdr(tok),
    )
    assert r.status_code == 404
