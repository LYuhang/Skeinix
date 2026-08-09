from __future__ import annotations

from unittest.mock import Mock

import pytest

from vibecanvas_api.security.crypto_core import (
    AwsKmsWrappingKeyProvider,
    DekCache,
    SecretUnavailableError,
    WrappedKey,
    keyed_lookup_digest,
)


def test_keyed_lookup_digest_is_normalized_and_domain_separated(monkeypatch):
    from vibecanvas_api.security import crypto_core

    monkeypatch.setattr(
        crypto_core.config,
        "content_lookup_hmac_key",
        "independent-test-lookup-key",
    )
    first = keyed_lookup_digest(
        domain="identity:v1",
        components=("password",),
        value=" User@Example.COM ",
        casefold=True,
    )
    assert first == keyed_lookup_digest(
        domain="identity:v1",
        components=("password",),
        value="user@example.com",
        casefold=True,
    )
    assert first != keyed_lookup_digest(
        domain="content:v1",
        components=("password",),
        value="user@example.com",
        casefold=True,
    )
    assert "user@example.com" not in first


def test_dek_cache_is_bounded_and_rejects_invalid_keys():
    cache = DekCache(ttl_seconds=60, max_entries=1)
    cache.put("first", b"a" * 32)
    cache.put("second", b"b" * 32)
    assert cache.get("first") is None
    assert cache.get("second") == b"b" * 32
    cache.discard("second")
    assert cache.get("second") is None
    with pytest.raises(SecretUnavailableError):
        cache.put("short", b"not-a-dek")


@pytest.mark.asyncio
async def test_aws_kms_outage_fails_closed_without_secret_details():
    provider = object.__new__(AwsKmsWrappingKeyProvider)
    provider._key_id = "alias/test"  # noqa: SLF001 - provider failure seam
    provider._client = Mock()  # noqa: SLF001 - no real cloud client in unit test
    provider._client.encrypt.side_effect = RuntimeError("sensitive request body")
    provider._client.decrypt.side_effect = RuntimeError("sensitive ciphertext")
    context = {"tenant_id": "tenant-a", "purpose": "test"}

    with pytest.raises(SecretUnavailableError, match="KMS wrap operation failed") as wrap:
        await provider.wrap_key(b"d" * 32, context=context)
    assert "sensitive" not in str(wrap.value)

    with pytest.raises(SecretUnavailableError, match="KMS unwrap operation failed") as unwrap:
        await provider.unwrap_key(
            WrappedKey(b"opaque", "alias/test", "alias/test"),
            context=context,
        )
    assert "sensitive" not in str(unwrap.value)
