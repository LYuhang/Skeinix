"""Cryptographic and claim-binding regressions for enterprise OIDC."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
import httpx
import pytest

from vibecanvas_api.auth import oidc
from vibecanvas_api.auth.oidc import (
    OidcError,
    OidcMetadata,
    OidcTransactionBundle,
)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _integer(value: int) -> str:
    return _b64url(value.to_bytes((value.bit_length() + 7) // 8, "big"))


def _signed_token(private_key, *, claims: dict, kid: str = "key-1") -> str:
    header = {"alg": "RS256", "kid": kid, "typ": "JWT"}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode()),
        _b64url(json.dumps(claims, separators=(",", ":")).encode()),
    ]
    signed = ".".join(segments).encode("ascii")
    signature = private_key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
    return f"{segments[0]}.{segments[1]}.{_b64url(signature)}"


@pytest.mark.asyncio
async def test_id_token_requires_signature_issuer_audience_nonce_and_time(
    monkeypatch,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwks = {
        "keys": [{
            "kty": "RSA",
            "kid": "key-1",
            "use": "sig",
            "alg": "RS256",
            "n": _integer(numbers.n),
            "e": _integer(numbers.e),
        }],
    }

    async def fake_get_json(_url: str, *, label: str):
        assert label == "OIDC JWKS endpoint"
        return jwks

    monkeypatch.setattr(oidc, "_get_json", fake_get_json)
    issuer = "https://login.example.com"
    metadata = OidcMetadata(
        issuer=issuer,
        authorization_endpoint=f"{issuer}/authorize",
        token_endpoint=f"{issuer}/token",
        jwks_uri=f"{issuer}/jwks",
    )
    provider = SimpleNamespace(client_id="vibecanvas-enterprise")
    now = datetime.now(timezone.utc)
    timestamp = int(now.timestamp())
    claims = {
        "iss": issuer,
        "aud": provider.client_id,
        "sub": "directory-subject",
        "nonce": "expected-nonce",
        "iat": timestamp,
        "exp": timestamp + 300,
    }
    token = _signed_token(private_key, claims=claims)
    validated = await oidc.validate_id_token(
        id_token=token,
        metadata=metadata,
        provider=provider,
        nonce="expected-nonce",
        now=now,
    )
    assert validated["sub"] == "directory-subject"

    cases = (
        ({**claims, "iss": "https://attacker.example"}, "oidc_issuer_mismatch"),
        ({**claims, "aud": "different-client"}, "oidc_audience_mismatch"),
        ({**claims, "nonce": "different-nonce"}, "oidc_nonce_mismatch"),
        ({**claims, "exp": timestamp - 120}, "oidc_token_expired"),
        ({**claims, "iat": timestamp + 120}, "oidc_token_time_invalid"),
    )
    for invalid_claims, expected in cases:
        with pytest.raises(OidcError, match=expected):
            await oidc.validate_id_token(
                id_token=_signed_token(private_key, claims=invalid_claims),
                metadata=metadata,
                provider=provider,
                nonce="expected-nonce",
                now=now,
            )

    header, payload, encoded_signature = token.split(".")
    signature = bytearray(base64.urlsafe_b64decode(
        encoded_signature + "=" * (-len(encoded_signature) % 4)
    ))
    signature[0] ^= 0x01
    corrupted = f"{header}.{payload}.{_b64url(bytes(signature))}"
    with pytest.raises(OidcError, match="oidc_signature_invalid"):
        await oidc.validate_id_token(
            id_token=corrupted,
            metadata=metadata,
            provider=provider,
            nonce="expected-nonce",
            now=now,
        )


def test_oidc_return_path_rejects_external_and_ambiguous_urls() -> None:
    assert oidc.validate_return_to("/workspace?from=sso") == "/workspace?from=sso"
    for value in (
        "https://attacker.example/",
        "//attacker.example/",
        "/\\attacker.example/",
        "workspace",
    ):
        with pytest.raises(OidcError, match="oidc_return_to_invalid"):
            oidc.validate_return_to(value)


@pytest.mark.asyncio
async def test_token_exchange_uses_discovered_standard_client_auth_method(
    monkeypatch,
) -> None:
    issuer = "https://login.example.com"
    metadata = OidcMetadata(
        issuer=issuer,
        authorization_endpoint=f"{issuer}/authorize",
        token_endpoint=f"{issuer}/token",
        jwks_uri=f"{issuer}/jwks",
        token_endpoint_auth_methods_supported=(
            "client_secret_basic",
            "client_secret_post",
        ),
    )
    provider = SimpleNamespace(
        issuer_url=issuer,
        client_id="enterprise-client",
        client_secret_ref="secret-ref",
        token_endpoint_auth_method="client_secret_basic",
    )

    async def fake_discover(_issuer: str) -> OidcMetadata:
        return metadata

    async def fake_secret(_session, _provider) -> str:
        return "client-secret"

    captured: list[dict] = []

    async def fake_request(method: str, url: str, **kwargs):
        captured.append({"method": method, "url": url, **kwargs})
        return httpx.Response(
            200,
            json={"id_token": "signed-token"},
            request=httpx.Request(method, url),
        )

    monkeypatch.setattr(oidc, "discover_oidc", fake_discover)
    monkeypatch.setattr(oidc, "_client_secret", fake_secret)
    monkeypatch.setattr(oidc, "request_pinned_public_url", fake_request)
    monkeypatch.setattr(
        oidc.config.public_urls,
        "public_url",
        "https://app.example.com/",
    )
    bundle = OidcTransactionBundle(
        code_verifier="v" * 64,
        nonce="nonce",
    )
    token, _ = await oidc.exchange_code(
        object(),
        provider=provider,
        code="authorization-code",
        bundle=bundle,
    )
    assert token == "signed-token"
    basic_request = captured[-1]
    assert basic_request["headers"]["Authorization"].startswith("Basic ")
    assert "client_secret" not in basic_request["data"]
    assert "client_id" not in basic_request["data"]

    provider.token_endpoint_auth_method = "client_secret_post"
    await oidc.exchange_code(
        object(),
        provider=provider,
        code="authorization-code",
        bundle=bundle,
    )
    post_request = captured[-1]
    assert "Authorization" not in post_request["headers"]
    assert post_request["data"]["client_id"] == "enterprise-client"
    assert post_request["data"]["client_secret"] == "client-secret"
