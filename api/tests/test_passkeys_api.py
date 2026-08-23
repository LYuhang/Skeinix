from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from webauthn.authentication.verify_authentication_response import (
    VerifiedAuthentication,
)
from webauthn.helpers import bytes_to_base64url
from webauthn.helpers.structs import (
    AttestationFormat,
    CredentialDeviceType,
    PublicKeyCredentialType,
)
from webauthn.registration.verify_registration_response import (
    VerifiedRegistration,
)

from vibecanvas_api.config import config
from vibecanvas_api.routes import webauthn as webauthn_routes
from vibecanvas_api.storage.models import (
    Session,
    UserWebAuthnCredential,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_authenticator_mfa_endpoints_are_not_registered(client) -> None:
    for method, path in (
        ("GET", "/api/v1/auth/mfa"),
        ("POST", "/api/v1/auth/mfa/totp/enroll"),
        ("POST", "/api/v1/auth/mfa/totp/confirm"),
        ("POST", "/api/v1/auth/mfa/challenge"),
        ("POST", "/api/v1/auth/login/mfa/totp"),
        ("POST", "/api/v1/auth/login/mfa/webauthn/options"),
        ("POST", "/api/v1/auth/login/mfa/webauthn/verify"),
    ):
        response = await client.request(method, path, json={})
        assert response.status_code == 404, (method, path, response.text)


async def test_enrolled_passkey_does_not_turn_password_login_into_mfa(
    client,
    app_engine,
) -> None:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "passkey-login@example.com",
            "username": "Passkey Login",
            "password": "correct horse battery staple",
        },
    )
    body = registered.json()
    token = body["session_token"]
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        session.add(
            UserWebAuthnCredential(
                credential_id="stored-passkey",
                user_id=uuid.UUID(body["user"]["user_id"]),
                tenant_id=uuid.UUID(
                    body["session"]["active_organization_id"]
                ),
                public_key=b"stored-public-key",
                sign_count=0,
                transports=["internal"],
                device_type="single_device",
                backed_up=False,
                name="Stored passkey",
            )
        )
        await session.commit()
    assert (
        await client.post("/api/v1/auth/logout", headers=_headers(token))
    ).status_code == 204

    login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "passkey-login@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert login.status_code == 200, login.text
    assert login.json()["session"]["authentication_strength"] == "password"
    assert "mfa_required" not in login.json()


async def test_webauthn_step_up_is_uv_bound_one_time_and_rotates_session(
    client,
    app_engine,
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "high_risk_step_up_required", True)
    credential_bytes = b"vibecanvas-test-credential"
    credential_id = bytes_to_base64url(credential_bytes)
    public_key = b"test-cose-public-key"
    captured: dict[str, object] = {}

    def fake_verify_registration(**kwargs):
        captured["registration"] = kwargs
        return VerifiedRegistration(
            credential_id=credential_bytes,
            credential_public_key=public_key,
            sign_count=3,
            aaguid="00000000-0000-0000-0000-000000000000",
            fmt=AttestationFormat.NONE,
            credential_type=PublicKeyCredentialType.PUBLIC_KEY,
            user_verified=True,
            attestation_object=b"attestation",
            credential_device_type=CredentialDeviceType.SINGLE_DEVICE,
            credential_backed_up=False,
        )

    def fake_verify_authentication(**kwargs):
        captured["authentication"] = kwargs
        return VerifiedAuthentication(
            credential_id=credential_bytes,
            new_sign_count=kwargs["credential_current_sign_count"] + 1,
            credential_device_type=CredentialDeviceType.SINGLE_DEVICE,
            credential_backed_up=False,
            user_verified=True,
        )

    monkeypatch.setattr(
        webauthn_routes,
        "verify_registration_response",
        fake_verify_registration,
    )
    monkeypatch.setattr(
        webauthn_routes,
        "verify_authentication_response",
        fake_verify_authentication,
    )
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "webauthn-flow@example.com",
            "username": "WebAuthn Flow",
            "password": "correct horse battery staple",
        },
    )
    assert registered.status_code == 201
    registered_body = registered.json()
    token = registered_body["session_token"]
    session_id = registered_body["session"]["session_id"]

    denied = await client.post(
        "/api/v1/auth/delete-account",
        headers=_headers(token),
        json={"email": "webauthn-flow@example.com"},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == {
        "code": "step_up_required",
        "method": "webauthn",
    }

    options = await client.post(
        "/api/v1/auth/passkeys/registration/options",
        headers=_headers(token),
        json={"password": "correct horse battery staple"},
    )
    assert options.status_code == 200, options.text
    assert options.headers["cache-control"] == "no-store"
    assert options.json()["rp"]["id"] == config.webauthn_rp_id
    assert options.json()["authenticatorSelection"]["userVerification"] == "required"

    registration_body = {
        "credential": {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": "AA",
                "attestationObject": "AA",
                "transports": ["internal"],
            },
        },
        "name": "Laptop passkey",
    }
    verified = await client.post(
        "/api/v1/auth/passkeys/registration/verify",
        headers=_headers(token),
        json=registration_body,
    )
    assert verified.status_code == 201, verified.text
    assert captured["registration"]["require_user_verification"] is True
    assert captured["registration"]["expected_origin"] == config.webauthn_origin
    token = verified.json()["session_token"]
    assert verified.json()["authentication_strength"] == "webauthn"
    assert verified.json()["step_up_expires_at"] is not None

    status = await client.get(
        "/api/v1/auth/passkeys",
        headers=_headers(token),
    )
    assert status.status_code == 200
    assert status.json()["credentials"] == [
        {
            "credential_id": credential_id,
            "name": "Laptop passkey",
            "device_type": "single_device",
            "backed_up": False,
            "transports": ["internal"],
            "created_at": status.json()["credentials"][0]["created_at"],
            "last_used_at": None,
        }
    ]

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        credential = await session.get(UserWebAuthnCredential, credential_id)
        assert credential is not None
        assert bytes(credential.public_key) == public_key
        assert "public_key" not in status.json()["credentials"][0]
        await session.execute(
            update(Session)
            .where(Session.session_id == session_id)
            .values(
                step_up_expires_at=datetime.now(timezone.utc)
                - timedelta(seconds=1)
            )
        )
        await session.commit()

    expired = await client.post(
        "/api/v1/auth/delete-account",
        headers=_headers(token),
        json={"email": "webauthn-flow@example.com"},
    )
    assert expired.status_code == 403

    authentication_options = await client.post(
        "/api/v1/auth/passkeys/authentication/options",
        headers=_headers(token),
    )
    assert authentication_options.status_code == 200
    assert authentication_options.json()["userVerification"] == "required"
    assert authentication_options.json()["allowCredentials"][0]["id"] == credential_id

    authentication_body = {
        "credential": {
            "id": credential_id,
            "rawId": credential_id,
            "type": "public-key",
            "response": {
                "clientDataJSON": "AA",
                "authenticatorData": "AA",
                "signature": "AA",
                "userHandle": None,
            },
        }
    }
    authenticated = await client.post(
        "/api/v1/auth/passkeys/authentication/verify",
        headers=_headers(token),
        json=authentication_body,
    )
    assert authenticated.status_code == 200, authenticated.text
    assert captured["authentication"]["require_user_verification"] is True
    assert captured["authentication"]["credential_current_sign_count"] == 3
    token = authenticated.json()["session_token"]

    replay = await client.post(
        "/api/v1/auth/passkeys/authentication/verify",
        headers=_headers(token),
        json=authentication_body,
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "webauthn_challenge_invalid_or_expired"

    deleted = await client.request(
        "DELETE",
        f"/api/v1/auth/passkeys/credentials/{credential_id}",
        headers=_headers(token),
        json={"password": "correct horse battery staple"},
    )
    assert deleted.status_code == 204, deleted.text
    old_session = await client.get(
        "/api/v1/auth/passkeys",
        headers=_headers(token),
    )
    assert old_session.status_code == 401
