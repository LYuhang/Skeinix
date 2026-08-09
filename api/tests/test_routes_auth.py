import re

import pytest


@pytest.mark.asyncio
async def test_register_then_me(client):
    r = await client.post("/api/v1/auth/register",
                          json={"email": "z@example.com", "username": "Test User", "password": "pw12345678"})
    assert r.status_code == 201
    token = r.json()["session_token"]
    me = await client.get("/api/v1/auth/me",
                          headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200 and me.json()["email"] == "z@example.com"


@pytest.mark.asyncio
async def test_login_wrong_password_generic_error(client):
    await client.post("/api/v1/auth/register",
                      json={"email": "y@example.com", "username": "Test User", "password": "pw12345678"})
    r = await client.post("/api/v1/auth/login",
                          json={"email": "y@example.com", "password": "WRONG"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"  # generic


@pytest.mark.asyncio
async def test_enabled_test_user_login_autocreates_test_account(client, monkeypatch):
    from vibecanvas_api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.app_config, "enable_test_user", True)
    r = await client.post("/api/v1/auth/login",
                          json={"email": "test", "password": "test"})
    assert r.status_code == 200
    data = r.json()
    assert data["user"]["email"] == "test@test.local"
    assert data["user"]["display_name"] == "test"

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {data['session_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == "test@test.local"
    organizations = await client.get(
        "/api/v1/organizations",
        headers={"Authorization": f"Bearer {data['session_token']}"},
    )
    assert organizations.status_code == 200
    active = next(
        item for item in organizations.json()["items"] if item["active"]
    )
    assert active["kind"] == "personal"
    assert active["name"] == "Personal workspace"
    assert all(item["kind"] != "business" for item in organizations.json()["items"])


@pytest.mark.asyncio
async def test_disabled_test_user_alias_is_rejected(client, monkeypatch):
    from vibecanvas_api.routes import auth as auth_routes

    monkeypatch.setattr(auth_routes.app_config, "enable_test_user", False)
    r = await client.post("/api/v1/auth/login",
                          json={"email": "test", "password": "test"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_logout_invalidates_token(client):
    r = await client.post("/api/v1/auth/register",
                          json={"email": "q@example.com", "username": "Test User", "password": "pw12345678"})
    token = r.json()["session_token"]
    h = {"Authorization": f"Bearer {token}"}
    await client.post("/api/v1/auth/logout", headers=h)
    me = await client.get("/api/v1/auth/me", headers=h)
    assert me.status_code == 401


@pytest.mark.asyncio
async def test_register_short_password_rejected(client):
    r = await client.post("/api/v1/auth/register",
                          json={"email": "s@example.com", "username": "Test User", "password": "short"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_password_reset_flow_is_one_shot(client, capsys):
    await client.post("/api/v1/auth/register",
                      json={"email": "r@example.com", "username": "Test User", "password": "pw12345678"})
    rr = await client.post("/api/v1/auth/password-reset/request",
                           json={"email": "r@example.com"})
    assert rr.status_code == 200
    # DevEmailSender prints the reset token to stderr; extract it.
    err = capsys.readouterr().err
    m = re.search(r"Reset token \(valid for 30 minutes\): (\S+)", err)
    assert m, f"reset token not found in stderr: {err!r}"
    reset_token = m.group(1)
    # first confirm succeeds
    c1 = await client.post("/api/v1/auth/password-reset/confirm",
                           json={"reset_token": reset_token,
                                 "new_password": "newpw12345678"})
    assert c1.status_code == 200
    # second confirm with the SAME token is rejected — one-shot
    c2 = await client.post("/api/v1/auth/password-reset/confirm",
                           json={"reset_token": reset_token,
                                 "new_password": "another12345"})
    assert c2.status_code == 400


@pytest.mark.asyncio
async def test_register_duplicate_email_409(client, monkeypatch):
    """A registration that races past the find_identity check and hits
    the keyed identity lookup UNIQUE constraint must surface a clean 409,
    not a 500."""
    from vibecanvas_api.auth.repo import AuthRepo

    first = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "dup@example.com",
            "username": "First User",
            "password": "pw12345678",
        },
    )
    assert first.status_code == 201
    original = AuthRepo.find_identity
    skipped = False

    async def lose_precheck_once(self, provider, provider_uid):
        nonlocal skipped
        if provider_uid == "dup@example.com" and not skipped:
            skipped = True
            return None
        return await original(self, provider, provider_uid)

    monkeypatch.setattr(AuthRepo, "find_identity", lose_precheck_once)
    r = await client.post("/api/v1/auth/register",
                          json={"email": "dup@example.com", "username": "Test User", "password": "pw12345678"})
    assert r.status_code == 409
