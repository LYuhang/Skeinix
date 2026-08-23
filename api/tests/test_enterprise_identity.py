"""Enterprise OIDC/SCIM lifecycle and local-write boundary regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import text

from vibecanvas_api.auth.oidc import OidcMetadata
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.auth.tokens import new_token
from vibecanvas_api.config import config
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models import Session


pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _enable_enterprise_sso_for_enterprise_tests(monkeypatch):
    """Existing enterprise lifecycle tests exercise the explicit opt-in mode."""
    monkeypatch.setattr(config, "enterprise_sso_enabled", True)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _scim_headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/scim+json",
    }


async def _enterprise_owner(client, *, label: str) -> tuple[str, str, str]:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{label}-{uuid.uuid4().hex[:10]}@example.com",
            "username": label,
            "password": "pw12345678",
        },
    )
    assert registered.status_code == 201, registered.text
    token = registered.json()["session_token"]
    user_id = registered.json()["user"]["user_id"]
    slug = f"{label}-{uuid.uuid4().hex[:10]}"
    created = await client.post(
        "/api/v1/organizations",
        headers=_headers(token),
        json={"name": "Enterprise Security", "slug": slug},
    )
    assert created.status_code == 201, created.text
    organization_id = created.json()["organization_id"]
    switched = await client.post(
        "/api/v1/organizations/active",
        headers=_headers(token),
        json={"organization_id": organization_id},
    )
    assert switched.status_code == 200, switched.text
    return token, user_id, organization_id


async def _create_provider(
    client,
    monkeypatch,
    *,
    token: str,
    organization_id: str,
    issuer: str = "https://idp.example.com",
) -> dict:
    from vibecanvas_api.routes import enterprise_identity as routes

    async def fake_discovery(value: str) -> OidcMetadata:
        assert value == issuer
        return OidcMetadata(
            issuer=issuer,
            authorization_endpoint=f"{issuer}/authorize",
            token_endpoint=f"{issuer}/token",
            jwks_uri=f"{issuer}/jwks",
        )

    monkeypatch.setattr(routes, "discover_oidc", fake_discovery)
    monkeypatch.setattr(
        config.public_urls,
        "public_url",
        "https://app.example.com/",
    )
    response = await client.post(
        f"/api/v1/organizations/{organization_id}/identity-providers",
        headers=_headers(token),
        json={
            "display_name": "Corporate Identity",
            "issuer_url": issuer,
            "client_id": "vibecanvas-enterprise",
            "client_secret": "oidc-client-secret-not-plaintext",
            "scim_token_ttl_days": 30,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_scim_user_group_lifecycle_is_encrypted_and_idp_read_only(
    client,
    pg_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "high_risk_step_up_required", False)
    token, _owner_id, organization_id = await _enterprise_owner(
        client,
        label="scim-owner",
    )
    provider = await _create_provider(
        client,
        monkeypatch,
        token=token,
        organization_id=organization_id,
    )
    provider_id = provider["provider_id"]
    scim_token = provider["scim_token"]
    scim_base = f"/scim/v2/{provider_id}"

    service_config = await client.get(
        f"{scim_base}/ServiceProviderConfig",
        headers=_scim_headers(scim_token),
    )
    assert service_config.status_code == 200, service_config.text
    assert service_config.headers["content-type"].startswith(
        "application/scim+json"
    )

    malformed = await client.post(
        f"{scim_base}/Users",
        headers=_scim_headers(scim_token),
        content=b'{"userName":',
    )
    assert malformed.status_code == 400, malformed.text
    assert malformed.headers["content-type"].startswith("application/scim+json")
    assert malformed.json()["scimType"] == "invalidSyntax"

    external_id = f"directory-{uuid.uuid4().hex}"
    user_name = f"directory-{uuid.uuid4().hex[:12]}@example.com"
    display_name = "Directory User Secret Name"
    created_user = await client.post(
        f"{scim_base}/Users",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "externalId": external_id,
            "userName": user_name,
            "displayName": display_name,
            "emails": [{"value": user_name, "primary": True}],
            "active": True,
        },
    )
    assert created_user.status_code == 201, created_user.text
    directory_user_id = created_user.json()["id"]

    async with pg_engine.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT d.private_ciphertext, d.external_id_lookup_hash, "
                    "d.user_name_lookup_hash, u.profile_ciphertext, u.user_id "
                    "FROM enterprise_directory_users d "
                    "JOIN users u ON u.user_id=d.user_id "
                    "WHERE d.directory_user_id=:directory_user_id"
                ),
                {"directory_user_id": directory_user_id},
            )
        ).one()
        secret_rows = list((await connection.execute(
            text(
                "SELECT ciphertext FROM encrypted_secrets "
                "WHERE resource_type='enterprise_identity_provider'"
            )
        )).scalars())
    for private_value in (external_id, user_name, display_name):
        assert private_value not in stored.private_ciphertext
        assert private_value not in stored.profile_ciphertext
        assert all(private_value not in (value or "") for value in secret_rows)
    assert stored.external_id_lookup_hash not in {external_id, user_name}
    assert stored.user_name_lookup_hash not in {external_id, user_name}

    members = await client.get(
        f"/api/v1/organizations/{organization_id}/members",
        headers=_headers(token),
    )
    assert members.status_code == 200, members.text
    directory_member = next(
        item for item in members.json()["items"]
        if item["user_id"] == str(stored.user_id)
    )
    assert directory_member["source"] == "scim"
    assert directory_member["directory_provider_id"] == provider_id

    group_external_id = f"group-{uuid.uuid4().hex}"
    created_group = await client.post(
        f"{scim_base}/Groups",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
            "externalId": group_external_id,
            "displayName": "Security Engineering",
            "members": [{"value": directory_user_id, "type": "User"}],
        },
    )
    assert created_group.status_code == 201, created_group.text
    group_id = created_group.json()["id"]
    assert created_group.json()["members"][0]["value"] == directory_user_id

    groups = await client.get(
        f"/api/v1/organizations/{organization_id}/groups",
        headers=_headers(token),
    )
    assert groups.status_code == 200, groups.text
    projected = next(
        item for item in groups.json()["items"] if item["group_id"] == group_id
    )
    assert projected["source"] == "idp"
    assert projected["directory_provider_id"] == provider_id
    assert projected["external_id"] == group_external_id

    local_rename = await client.patch(
        f"/api/v1/organizations/{organization_id}/groups/{group_id}",
        headers=_headers(token),
        json={"name": "Local Rename Must Fail"},
    )
    assert local_rename.status_code == 409, local_rename.text
    assert local_rename.json()["detail"] == "idp_managed_group_read_only"
    local_member_write = await client.put(
        f"/api/v1/organizations/{organization_id}/groups/{group_id}/members/"
        f"{stored.user_id}",
        headers=_headers(token),
        json={"role": "member", "status": "active"},
    )
    assert local_member_write.status_code == 409, local_member_write.text

    raw_directory_session, directory_session_hash = new_token()
    async with session_scope(tenant_id=organization_id) as session:
        await AuthRepo(session).create_session(
            directory_session_hash,
            stored.user_id,
            organization_id,
            datetime.now(timezone.utc) + timedelta(hours=1),
            audience="web",
            active_organization_id=organization_id,
            authentication_strength="oauth",
        )
    assert raw_directory_session

    deactivated = await client.patch(
        f"{scim_base}/Users/{directory_user_id}",
        headers=_scim_headers(scim_token),
        json={
            "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
            "Operations": [{"op": "replace", "path": "active", "value": False}],
        },
    )
    assert deactivated.status_code == 200, deactivated.text
    assert deactivated.json()["active"] is False
    async with session_scope() as session:
        assert await session.get(Session, directory_session_hash) is None

    old_token = scim_token
    rotated = await client.post(
        f"/api/v1/organizations/{organization_id}/identity-providers/"
        f"{provider_id}/scim-token",
        headers=_headers(token),
        json={"ttl_days": 30},
    )
    assert rotated.status_code == 200, rotated.text
    new_token_value = rotated.json()["scim_token"]
    assert new_token_value != old_token
    rejected_old = await client.get(
        f"{scim_base}/Users",
        headers=_scim_headers(old_token),
    )
    assert rejected_old.status_code == 401
    accepted_new = await client.get(
        f"{scim_base}/Users",
        headers=_scim_headers(new_token_value),
    )
    assert accepted_new.status_code == 200, accepted_new.text


async def test_scim_rejects_cross_provider_group_membership(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "high_risk_step_up_required", False)
    token, _owner_id, organization_id = await _enterprise_owner(
        client,
        label="scim-cross-provider",
    )
    first = await _create_provider(
        client,
        monkeypatch,
        token=token,
        organization_id=organization_id,
        issuer="https://first-idp.example.com",
    )
    second = await _create_provider(
        client,
        monkeypatch,
        token=token,
        organization_id=organization_id,
        issuer="https://second-idp.example.com",
    )
    first_user = await client.post(
        f"/scim/v2/{first['provider_id']}/Users",
        headers=_scim_headers(first["scim_token"]),
        json={
            "userName": "first-user@example.com",
            "externalId": f"first-{uuid.uuid4().hex}",
            "emails": [{"value": "first-user@example.com"}],
        },
    )
    assert first_user.status_code == 201, first_user.text
    cross_provider = await client.post(
        f"/scim/v2/{second['provider_id']}/Groups",
        headers=_scim_headers(second["scim_token"]),
        json={
            "displayName": "Cross Provider Group",
            "externalId": f"group-{uuid.uuid4().hex}",
            "members": [{"value": first_user.json()["id"], "type": "User"}],
        },
    )
    assert cross_provider.status_code == 400, cross_provider.text
    assert cross_provider.json()["scimType"] == "invalidValue"


async def test_oidc_pkce_callback_creates_oauth_session_and_state_is_one_time(
    client,
    monkeypatch,
) -> None:
    from vibecanvas_api.auth import oidc
    from vibecanvas_api.routes import enterprise_identity as routes

    monkeypatch.setattr(config, "high_risk_step_up_required", False)
    token, _owner_id, organization_id = await _enterprise_owner(
        client,
        label="oidc-owner",
    )
    issuer = "https://login.example.com"
    provider = await _create_provider(
        client,
        monkeypatch,
        token=token,
        organization_id=organization_id,
        issuer=issuer,
    )
    provider_id = provider["provider_id"]
    external_id = f"oidc-subject-{uuid.uuid4().hex}"
    email = f"oidc-{uuid.uuid4().hex[:10]}@example.com"
    provisioned = await client.post(
        f"/scim/v2/{provider_id}/Users",
        headers=_scim_headers(provider["scim_token"]),
        json={
            "externalId": external_id,
            "userName": email,
            "emails": [{"value": email, "primary": True}],
            "active": True,
        },
    )
    assert provisioned.status_code == 201, provisioned.text

    metadata = OidcMetadata(
        issuer=issuer,
        authorization_endpoint=f"{issuer}/authorize",
        token_endpoint=f"{issuer}/token",
        jwks_uri=f"{issuer}/jwks",
    )

    async def fake_discover(value: str) -> OidcMetadata:
        assert value == issuer
        return metadata

    monkeypatch.setattr(oidc, "discover_oidc", fake_discover)
    monkeypatch.setattr(config, "web_session_cookie_enabled", True)

    return_to = "/workspace?from=sso"
    started = await client.get(
        f"/api/v1/auth/sso/providers/{provider_id}/start",
        params={"return_to": return_to},
    )
    assert started.status_code == 302, started.text
    authorization_query = parse_qs(urlsplit(started.headers["location"]).query)
    state = authorization_query["state"][0]
    nonce = authorization_query["nonce"][0]
    assert authorization_query["code_challenge_method"] == ["S256"]
    assert len(authorization_query["code_challenge"][0]) >= 43

    async def fake_exchange_code(
        _session,
        *,
        provider,
        code: str,
        bundle,
    ):
        assert str(provider.provider_id) == provider_id
        assert code == "authorization-code"
        assert len(bundle.code_verifier) >= 43
        assert bundle.nonce == nonce
        return "signed-id-token", metadata

    async def fake_validate_id_token(
        *,
        id_token: str,
        metadata,
        provider,
        nonce: str,
    ):
        assert id_token == "signed-id-token"
        assert metadata.issuer == issuer
        assert str(provider.provider_id) == provider_id
        assert nonce == authorization_query["nonce"][0]
        now = int(datetime.now(timezone.utc).timestamp())
        return {
            "iss": issuer,
            "aud": provider.client_id,
            "sub": external_id,
            "email": email,
            "name": "OIDC Directory User",
            "nonce": nonce,
            "iat": now,
            "exp": now + 300,
        }

    monkeypatch.setattr(routes, "exchange_code", fake_exchange_code)
    monkeypatch.setattr(routes, "validate_id_token", fake_validate_id_token)
    completed = await client.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "authorization-code"},
    )
    assert completed.status_code == 303, completed.text
    assert completed.headers["location"] == return_to
    web_session_cookie = next(
        cookie for cookie in client.cookies.jar
        if cookie.name.endswith("vibecanvas-web-session")
    )
    assert web_session_cookie.value

    # The production cookie is Secure; ASGITransport's test origin is HTTP,
    # so supply the exact cookie explicitly instead of weakening its flags.
    me = await client.get(
        "/api/v1/auth/me",
        headers={
            "Cookie": f"{web_session_cookie.name}={web_session_cookie.value}",
        },
    )
    assert me.status_code == 200, me.text
    assert me.json()["active_organization_id"] == organization_id
    assert me.json()["session"]["authentication_strength"] == "oauth"

    replay = await client.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "authorization-code"},
    )
    assert replay.status_code == 400, replay.text
    assert replay.json()["detail"] == "oidc_transaction_missing"

    invalid_return = await client.get(
        f"/api/v1/auth/sso/providers/{provider_id}/start",
        params={"return_to": "https://attacker.example/steal"},
    )
    assert invalid_return.status_code == 400
    assert invalid_return.json()["detail"] == "oidc_return_to_invalid"


async def test_enterprise_identity_schema_is_at_current_head(pg_engine) -> None:
    from pathlib import Path

    from alembic.config import Config as AlembicConfig
    from alembic.script import ScriptDirectory

    api_root = Path(__file__).resolve().parents[1]
    alembic_config = AlembicConfig(str(api_root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(api_root / "alembic"))
    expected_head = ScriptDirectory.from_config(alembic_config).get_current_head()
    async with pg_engine.connect() as connection:
        revision = (await connection.execute(
            text("SELECT version_num FROM alembic_version")
        )).scalar_one()
        columns = set((await connection.execute(text(
            "SELECT table_name || '.' || column_name "
            "FROM information_schema.columns WHERE table_name IN "
            "('enterprise_identity_providers','enterprise_directory_users',"
            "'oidc_login_transactions')"
        ))).scalars())
    assert revision == expected_head
    assert {
        "enterprise_identity_providers.organization_slug",
        "enterprise_identity_providers.scim_token_hash",
        "enterprise_directory_users.private_ciphertext",
        "enterprise_directory_users.private_key_id",
        "oidc_login_transactions.state_hash",
        "oidc_login_transactions.secret_ref",
    } <= columns


async def test_public_sso_discovery_has_distributed_abuse_boundary(client) -> None:
    for _ in range(30):
        response = await client.get(
            "/api/v1/auth/sso/organizations/not-configured/providers"
        )
        assert response.status_code == 200
        assert response.json() == {"items": []}
    limited = await client.get(
        "/api/v1/auth/sso/organizations/not-configured/providers"
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "300"
    assert limited.json()["detail"] == "sso_rate_limited"


async def test_public_sso_routes_fail_closed_when_feature_is_disabled(
    client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "enterprise_sso_enabled", False)
    discovery = await client.get(
        "/api/v1/auth/sso/organizations/not-configured/providers"
    )
    start = await client.get(
        f"/api/v1/auth/sso/providers/{uuid.uuid4()}/start"
    )
    callback = await client.get(
        "/api/v1/auth/sso/callback",
        params={"state": "x" * 32},
    )
    for response in (discovery, start, callback):
        assert response.status_code == 404
        assert response.json()["detail"] == "enterprise_sso_disabled"
