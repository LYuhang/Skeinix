from __future__ import annotations

import base64
from unittest.mock import AsyncMock
import uuid

import pytest
from sqlalchemy import select, text, update

from vibecanvas_api.security.secret_service import (
    LocalWrappingKeyProvider,
    SecretIntegrityError,
    SecretService,
    WrappedKey,
    suppress_secret_creation_audit,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models_secrets import EncryptedSecret


class _CountingWrappingProvider(LocalWrappingKeyProvider):
    def __init__(self, key: bytes):
        super().__init__(key)
        self.wrap_count = 0
        self.unwrap_count = 0

    async def wrap_key(self, plaintext_dek: bytes, *, context):
        self.wrap_count += 1
        return await super().wrap_key(plaintext_dek, context=context)

    async def unwrap_key(self, wrapped, *, context):
        self.unwrap_count += 1
        return await super().unwrap_key(wrapped, context=context)


@pytest.mark.asyncio
async def test_secret_backfill_audit_suppression_is_context_local(
    app_engine,
    monkeypatch,
):
    """The deployment backfill can pause audits without changing runtime writes."""
    import vibecanvas_api.security.secret_service as secret_service_module

    tenant_id = uuid.uuid4()
    async with app_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:id,'audit-scope')"),
            {"id": tenant_id},
        )

    audit = AsyncMock()
    monkeypatch.setattr(secret_service_module, "record_audit", audit)
    service = SecretService(LocalWrappingKeyProvider(b"a" * 32))
    with suppress_secret_creation_audit():
        async with session_scope(tenant_id=str(tenant_id)) as session:
            await service.put_text(
                session,
                tenant_id=tenant_id,
                purpose="migration_only",
                resource_type="migration_probe",
                resource_id=uuid.uuid4(),
                plaintext="legacy value",
            )
    audit.assert_not_awaited()

    async with session_scope(tenant_id=str(tenant_id)) as session:
        await service.put_text(
            session,
            tenant_id=tenant_id,
            purpose="runtime_write",
            resource_type="runtime_probe",
            resource_id=uuid.uuid4(),
            plaintext="new value",
        )
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_wrapping_key_is_context_bound():
    provider = LocalWrappingKeyProvider(b"k" * 32)
    context = {"tenant_id": "tenant-a", "purpose": "test"}
    wrapped = await provider.wrap_key(b"d" * 32, context=context)

    assert await provider.unwrap_key(wrapped, context=context) == b"d" * 32
    tampered_ciphertext = wrapped.ciphertext[:-1] + bytes(
        [wrapped.ciphertext[-1] ^ 0x01]
    )
    with pytest.raises(SecretIntegrityError):
        await provider.unwrap_key(
            WrappedKey(
                ciphertext=tampered_ciphertext,
                key_id=wrapped.key_id,
                key_version=wrapped.key_version,
            ),
            context=context,
        )
    with pytest.raises(SecretIntegrityError):
        await provider.unwrap_key(
            wrapped,
            context={"tenant_id": "tenant-b", "purpose": "test"},
        )


@pytest.mark.asyncio
async def test_envelope_secret_round_trip_ciphertext_and_crypto_shred(app_engine):
    tenant_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    async with app_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:id,'secret-test')"),
            {"id": tenant_id},
        )

    service = SecretService(LocalWrappingKeyProvider(b"m" * 32))
    async with session_scope(tenant_id=str(tenant_id)) as session:
        secret_ref = await service.put_text(
            session,
            tenant_id=tenant_id,
            purpose="llm_api_key",
            resource_type="llm_credential",
            resource_id=resource_id,
            plaintext="test-only-never-store-this-plaintext",
        )
        row = await session.get(EncryptedSecret, secret_ref)
        assert row is not None
        assert "test-only-never-store" not in (row.ciphertext or "")
        assert len(base64.b64decode(row.nonce or "")) == 12
        assert await service.resolve_text(
            session,
            secret_ref=secret_ref,
            tenant_id=tenant_id,
            purpose="llm_api_key",
            resource_type="llm_credential",
            resource_id=resource_id,
        ) == "test-only-never-store-this-plaintext"

        await service.destroy(
            session,
            secret_ref=secret_ref,
            tenant_id=tenant_id,
        )
        await session.refresh(row)
        assert row.status == "destroyed"
        assert row.ciphertext is None
        assert row.wrapped_dek is None


@pytest.mark.asyncio
async def test_ciphertext_tamper_and_cross_tenant_are_rejected(app_engine):
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    resource_id = uuid.uuid4()
    async with app_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO tenants(tenant_id,name) VALUES "
                "(:a,'secret-a'),(:b,'secret-b')"
            ),
            {"a": tenant_a, "b": tenant_b},
        )

    service = SecretService(LocalWrappingKeyProvider(b"z" * 32))
    async with session_scope(tenant_id=str(tenant_a)) as session:
        secret_ref = await service.put_text(
            session,
            tenant_id=tenant_a,
            purpose="test_secret",
            resource_type="test_resource",
            resource_id=resource_id,
            plaintext="sensitive",
        )
        row = await session.get(EncryptedSecret, secret_ref)
        assert row is not None
        tampered = bytearray(base64.b64decode(row.ciphertext or ""))
        tampered[-1] ^= 1
        await session.execute(
            update(EncryptedSecret)
            .where(EncryptedSecret.secret_id == secret_ref)
            .values(ciphertext=base64.b64encode(tampered).decode())
        )
        with pytest.raises(SecretIntegrityError):
            await service.resolve_text(
                session,
                secret_ref=secret_ref,
                tenant_id=tenant_a,
                purpose="test_secret",
                resource_type="test_resource",
                resource_id=resource_id,
            )

    async with session_scope(tenant_id=str(tenant_b)) as session:
        rows = (
            await session.execute(select(EncryptedSecret))
        ).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_secret_dek_cache_avoids_repeated_kms_unwraps(app_engine):
    tenant_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    async with app_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:id,'cache-test')"),
            {"id": tenant_id},
        )

    writer_provider = _CountingWrappingProvider(b"c" * 32)
    writer = SecretService(writer_provider)
    async with session_scope(tenant_id=str(tenant_id)) as session:
        secret_ref = await writer.put_text(
            session,
            tenant_id=tenant_id,
            purpose="cache_test",
            resource_type="test_resource",
            resource_id=resource_id,
            plaintext="cached secret",
        )
    assert writer_provider.wrap_count == 1

    reader_provider = _CountingWrappingProvider(b"c" * 32)
    reader = SecretService(reader_provider, cache_ttl_seconds=60, cache_size=4)
    for _ in range(3):
        async with session_scope(tenant_id=str(tenant_id)) as session:
            assert await reader.resolve_text(
                session,
                secret_ref=secret_ref,
                tenant_id=tenant_id,
                purpose="cache_test",
                resource_type="test_resource",
                resource_id=resource_id,
            ) == "cached secret"

    # Every request revalidates the durable secret row, while the expensive
    # KMS unwrap is shared through the bounded process-local DEK cache.
    assert reader_provider.unwrap_count == 1
