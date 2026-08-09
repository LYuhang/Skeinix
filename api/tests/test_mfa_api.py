from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
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

from vibecanvas_api.auth.mfa import TOTP_PERIOD_SECONDS, totp_code
from vibecanvas_api.config import config
from vibecanvas_api.routes import auth as auth_routes
from vibecanvas_api.routes import webauthn as webauthn_routes
from vibecanvas_api.storage.models import (
    Session,
    UserLoginMfaChallenge,
    UserMfaTotp,
    UserWebAuthnCredential,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_totp_enrollment_replay_recovery_and_ciphertext_storage(
    client,
    app_engine,
) -> None:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "mfa-flow@example.com",
            "username": "MFA Flow",
            "password": "correct horse battery staple",
        },
    )
    assert registered.status_code == 201
    token = registered.json()["session_token"]

    enrolled = await client.post(
        "/api/v1/auth/mfa/totp/enroll",
        headers=_headers(token),
        json={"password": "correct horse battery staple"},
    )
    assert enrolled.status_code == 201
    assert enrolled.headers["cache-control"] == "no-store"
    secret = enrolled.json()["secret"]
    assert secret not in enrolled.json()["provisioning_uri"].split("secret=", 1)[0]

    current_step = int(time.time()) // TOTP_PERIOD_SECONDS
    confirmed = await client.post(
        "/api/v1/auth/mfa/totp/confirm",
        headers=_headers(token),
        json={"code": totp_code(secret, step=current_step)},
    )
    assert confirmed.status_code == 200, confirmed.text
    confirmation = confirmed.json()
    assert confirmation["authentication_strength"] == "totp"
    assert confirmation["step_up_expires_at"] is None
    assert len(confirmation["recovery_codes"]) == 10
    assert confirmation["session_token"] != token
    token = confirmation["session_token"]

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        row = (
            await session.execute(select(UserMfaTotp))
        ).scalar_one()
        assert row.status == "active"
        assert secret not in row.secret_ciphertext
        assert all(
            code not in row.recovery_code_hashes
            for code in confirmation["recovery_codes"]
        )

    replayed = await client.post(
        "/api/v1/auth/mfa/challenge",
        headers=_headers(token),
        json={"code": totp_code(secret, step=current_step)},
    )
    assert replayed.status_code == 400
    assert replayed.json()["detail"] == "mfa_code_invalid_or_replayed"

    next_totp = await client.post(
        "/api/v1/auth/mfa/challenge",
        headers=_headers(token),
        json={"code": totp_code(secret, step=current_step + 1)},
    )
    assert next_totp.status_code == 200, next_totp.text
    assert next_totp.json()["authentication_strength"] == "totp"
    token = next_totp.json()["session_token"]

    recovery_code = confirmation["recovery_codes"][0]
    recovered = await client.post(
        "/api/v1/auth/mfa/challenge",
        headers=_headers(token),
        json={"code": recovery_code},
    )
    assert recovered.status_code == 200
    assert recovered.json()["authentication_strength"] == "recovery"
    assert recovered.json()["step_up_expires_at"] is None
    token = recovered.json()["session_token"]

    reused_recovery = await client.post(
        "/api/v1/auth/mfa/challenge",
        headers=_headers(token),
        json={"code": recovery_code},
    )
    assert reused_recovery.status_code == 400

    disabled = await client.request(
        "DELETE",
        "/api/v1/auth/mfa",
        headers=_headers(token),
        json={
            "password": "correct horse battery staple",
            "code": confirmation["recovery_codes"][1],
        },
    )
    assert disabled.status_code == 204, disabled.text
    status = await client.get(
        "/api/v1/auth/mfa",
        headers=_headers(token),
    )
    # Disabling rotates the Session, so the pre-disable token is invalid.
    assert status.status_code == 401


async def test_password_login_with_totp_factor_creates_no_pending_session(
    client,
    app_engine,
) -> None:
    registered = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "totp-login@example.com",
            "username": "TOTP Login",
            "password": "correct horse battery staple",
        },
    )
    token = registered.json()["session_token"]
    enrolled = await client.post(
        "/api/v1/auth/mfa/totp/enroll",
        headers=_headers(token),
        json={"password": "correct horse battery staple"},
    )
    secret = enrolled.json()["secret"]
    current_step = int(time.time()) // TOTP_PERIOD_SECONDS
    confirmed = await client.post(
        "/api/v1/auth/mfa/totp/confirm",
        headers=_headers(token),
        json={"code": totp_code(secret, step=current_step)},
    )
    assert confirmed.status_code == 200
    recovery_code = confirmed.json()["recovery_codes"][0]
    token = confirmed.json()["session_token"]
    assert (await client.post("/api/v1/auth/logout", headers=_headers(token))).status_code == 204

    password_only = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "totp-login@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert password_only.status_code == 202, password_only.text
    pending = password_only.json()
    assert pending["mfa_required"] is True
    assert pending["methods"] == ["totp", "recovery"]
    assert "session_token" not in pending
    assert password_only.headers["cache-control"] == "no-store"

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        assert list((await session.execute(select(Session))).scalars()) == []
        challenges = list(
            (await session.execute(select(UserLoginMfaChallenge))).scalars()
        )
        assert len(challenges) == 1
        assert pending["login_challenge"] not in challenges[0].token_hash

    completed = await client.post(
        "/api/v1/auth/login/mfa/totp",
        json={
            "login_challenge": pending["login_challenge"],
            "code": recovery_code,
        },
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["session"]["authentication_strength"] == "recovery"
    completed_token = completed.json()["session_token"]
    assert (
        await client.get("/api/v1/auth/me", headers=_headers(completed_token))
    ).status_code == 200

    replay = await client.post(
        "/api/v1/auth/login/mfa/totp",
        json={
            "login_challenge": pending["login_challenge"],
            "code": recovery_code,
        },
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "login_mfa_challenge_invalid_or_expired"

    assert (
        await client.post(
            "/api/v1/auth/logout",
            headers=_headers(completed_token),
        )
    ).status_code == 204
    limited_login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "totp-login@example.com",
            "password": "correct horse battery staple",
        },
    )
    limited_challenge = limited_login.json()["login_challenge"]
    for _ in range(5):
        invalid = await client.post(
            "/api/v1/auth/login/mfa/totp",
            json={
                "login_challenge": limited_challenge,
                "code": "not-a-valid-code",
            },
        )
        assert invalid.status_code == 400
    async with maker() as session:
        assert list(
            (await session.execute(select(UserLoginMfaChallenge))).scalars()
        ) == []
        assert list((await session.execute(select(Session))).scalars()) == []


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
    monkeypatch.setattr(
        auth_routes,
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
        "/api/v1/auth/mfa/webauthn/registration/options",
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
        "/api/v1/auth/mfa/webauthn/registration/verify",
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
        "/api/v1/auth/mfa/webauthn",
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
        "/api/v1/auth/mfa/webauthn/authentication/options",
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
        "/api/v1/auth/mfa/webauthn/authentication/verify",
        headers=_headers(token),
        json=authentication_body,
    )
    assert authenticated.status_code == 200, authenticated.text
    assert captured["authentication"]["require_user_verification"] is True
    assert captured["authentication"]["credential_current_sign_count"] == 3
    token = authenticated.json()["session_token"]

    replay = await client.post(
        "/api/v1/auth/mfa/webauthn/authentication/verify",
        headers=_headers(token),
        json=authentication_body,
    )
    assert replay.status_code == 400
    assert replay.json()["detail"] == "webauthn_challenge_invalid_or_expired"

    logged_out = await client.post(
        "/api/v1/auth/logout",
        headers=_headers(token),
    )
    assert logged_out.status_code == 204
    password_only = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "webauthn-flow@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert password_only.status_code == 202, password_only.text
    login_pending = password_only.json()
    assert login_pending["methods"] == ["webauthn"]
    assert login_pending["webauthn_options"]["userVerification"] == "required"
    assert "session_token" not in login_pending

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as session:
        assert list((await session.execute(select(Session))).scalars()) == []

    refreshed = await client.post(
        "/api/v1/auth/login/mfa/webauthn/options",
        json={"login_challenge": login_pending["login_challenge"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["allowCredentials"][0]["id"] == credential_id
    login_completed = await client.post(
        "/api/v1/auth/login/mfa/webauthn/verify",
        json={
            "login_challenge": login_pending["login_challenge"],
            **authentication_body,
        },
    )
    assert login_completed.status_code == 200, login_completed.text
    assert login_completed.json()["session"]["authentication_strength"] == "webauthn"
    assert login_completed.json()["session"]["step_up_expires_at"] is not None
    assert captured["authentication"]["credential_current_sign_count"] == 4
    token = login_completed.json()["session_token"]

    login_replay = await client.post(
        "/api/v1/auth/login/mfa/webauthn/verify",
        json={
            "login_challenge": login_pending["login_challenge"],
            **authentication_body,
        },
    )
    assert login_replay.status_code == 400

    deleted = await client.request(
        "DELETE",
        f"/api/v1/auth/mfa/webauthn/credentials/{credential_id}",
        headers=_headers(token),
        json={"password": "correct horse battery staple"},
    )
    assert deleted.status_code == 204, deleted.text
    old_session = await client.get(
        "/api/v1/auth/mfa/webauthn",
        headers=_headers(token),
    )
    assert old_session.status_code == 401
