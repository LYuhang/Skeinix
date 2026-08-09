"""Performance invariants for high-volume encrypted replay ledgers."""
from __future__ import annotations

import uuid

import pytest

from vibecanvas_api.security.content_encryption import ContentEncryptionService
from vibecanvas_api.security.secret_service import LocalWrappingKeyProvider


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _CountingSession:
    """Minimal AsyncSession seam used to count durable key validations."""

    def __init__(self, row=None):
        self.info: dict = {}
        self.row = row
        self.execute_count = 0

    async def execute(self, _statement):
        self.execute_count += 1
        return _ScalarResult(self.row)

    def add(self, row):
        self.row = row

    async def flush(self):
        return None


@pytest.mark.asyncio
async def test_replay_batch_validates_content_key_once_per_session():
    """Hundreds of events must not become hundreds of key-table/KMS calls.

    Authorization is deliberately outside this cache.  The invariant here is
    narrower: once one request-local database Session has verified the exact
    key/tenant/resource tuple, subsequent AES-GCM decryptions reuse the bounded
    DEK cache.  A new Session performs one fresh durable-row validation.
    """
    tenant_id = uuid.uuid4()
    service = ContentEncryptionService(
        LocalWrappingKeyProvider(b"k" * 32),
        cache_ttl_seconds=60,
        cache_size=4,
    )
    writer = _CountingSession()
    encrypted = await service.encrypt_bytes(
        writer,
        tenant_id=tenant_id,
        resource_type="chat",
        resource_id="chat-efficient-replay",
        purpose="agent_run_event",
        record_id="run-1:1",
        plaintext=b"ordered replay payload",
    )
    assert writer.execute_count == 1

    for _ in range(250):
        plaintext = await service.decrypt_bytes(
            writer,
            key_id=encrypted.key_id,
            tenant_id=tenant_id,
            resource_type="chat",
            resource_id="chat-efficient-replay",
            purpose="agent_run_event",
            record_id="run-1:1",
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
        )
        assert plaintext == b"ordered replay payload"

    # One lookup when the resource key was created and one validation before
    # the first decrypt; the other 249 decryptions are local AES-GCM only.
    assert writer.execute_count == 2

    reader = _CountingSession(writer.row)
    for _ in range(250):
        await service.decrypt_bytes(
            reader,
            key_id=encrypted.key_id,
            tenant_id=tenant_id,
            resource_type="chat",
            resource_id="chat-efficient-replay",
            purpose="agent_run_event",
            record_id="run-1:1",
            ciphertext=encrypted.ciphertext,
            nonce=encrypted.nonce,
        )

    # Request/session boundaries revalidate durable key ownership exactly once.
    assert reader.execute_count == 1
