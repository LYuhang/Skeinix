"""Meta routes: /api/v1/version + /me + /enums.

Auth: the legacy ``VIBECANVAS_API_DEV_TOKEN`` + sync ``TestClient`` +
``Bearer tok`` harness is DEAD (dev-token auth was removed from the app).
The auth-gated route tests now use the conftest async ``client`` fixture +
a real ``register → session_token`` (the same pattern as
``test_routes_vfs.py`` / ``test_routes_executions.py``). ``/version`` is
unauthenticated and ``/me`` unauthenticated must still 401.
"""

from __future__ import annotations

import uuid

import pytest

from vibecanvas_api.config import config


async def _register(client) -> str:
    """Register a fresh user, return its bearer session token."""
    email = f"meta_{uuid.uuid4().hex[:12]}@example.com"
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": "Test User", "password": "pw12345678"},
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["session_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_version_no_auth(client, pg_engine):
    r = await client.get("/api/v1/version")
    assert r.status_code == 200
    body = r.json()
    assert "engine" in body and "api" in body


@pytest.mark.asyncio
async def test_public_config_exposes_only_login_capabilities(client, monkeypatch):
    monkeypatch.setattr(config, "enable_test_user", True)
    monkeypatch.setattr(config, "enterprise_sso_enabled", False)
    response = await client.get("/api/v1/public-config")
    assert response.status_code == 200
    assert response.json() == {
        "enable_test_user": True,
        "enterprise_sso_enabled": False,
    }


@pytest.mark.asyncio
async def test_me_requires_auth(client, pg_engine):
    r = await client.get("/api/v1/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_with_auth_returns_username(client, pg_engine):
    tok = await _register(client)
    r = await client.get("/api/v1/me", headers=_hdr(tok))
    assert r.status_code == 200
    assert "username" in r.json()


@pytest.mark.asyncio
async def test_enums_with_auth(client, pg_engine):
    tok = await _register(client)
    r = await client.get("/api/v1/enums", headers=_hdr(tok))
    assert r.status_code == 200
    assert "enums" in r.json()
    assert isinstance(r.json()["enums"], dict)


def test_enums_payload_has_no_code_libraries_enum():
    """CodeNode third-party libraries are declared per workflow in
    Workflow Settings (requirements.txt) and provisioned via the dependency
    overlay — there is no curated library enum anymore. The enums payload must
    NOT carry ``code_libraries_available``, and the surviving static enums stay.

    Unit-level (calls get_frontend_enums directly) so it does not depend on
    the route auth harness.
    """
    from vibecanvas_api.enums import get_frontend_enums

    payload = get_frontend_enums()
    assert "code_libraries_available" not in payload
    # the surviving static enums are still present
    assert "field_types" in payload
    assert "programming_languages" in payload
    assert "model_names" in payload
    assert "workflow_domains" in payload
