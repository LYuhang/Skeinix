"""KMS-backed, per-resource envelope encryption for private user content.

Application storage is ciphertext-only. Legacy columns are visible solely
while the deployment migrator is paused at an irreversible cutover revision;
runtime readers never dual-read or fall back to plaintext. One wrapped DEK is
cached briefly per resource so stream persistence does not perform a KMS
round-trip for every token, message, or event.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
from functools import lru_cache
from typing import Any
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.crypto_core import (
    AES_KEY_BYTES,
    GCM_NONCE_BYTES,
    DekCache,
    SecretIntegrityError,
    SecretUnavailableError,
    WrappedKey,
    WrappingKeyProvider,
    canonical_context,
    keyed_lookup_digest,
    wipe_bytes,
    wrapping_provider_from_config,
)
from vibecanvas_api.storage.models_content_keys import ContentEncryptionKey


@dataclass(frozen=True, slots=True)
class ContentCiphertext:
    key_id: uuid.UUID
    ciphertext: str
    nonce: str


def content_lookup_digest(
    *,
    tenant_id: uuid.UUID | str,
    namespace: str,
    value: str,
) -> str:
    """Return a keyed equality token without exposing private lookup text."""
    return keyed_lookup_digest(
        domain="vibecanvas:content-lookup:v1",
        components=(str(tenant_id), namespace),
        value=value,
    )


class ContentEncryptionService:
    def __init__(
        self,
        provider: WrappingKeyProvider | None = None,
        *,
        cache_ttl_seconds: float = 300.0,
        cache_size: int = 256,
    ) -> None:
        self._provider = provider or wrapping_provider_from_config()
        self._dek_cache = DekCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_size,
        )

    @staticmethod
    def _key_context(
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        resource_type: str,
        resource_id: str,
        version: int,
    ) -> dict[str, str]:
        return {
            "content_key_id": str(key_id),
            "tenant_id": str(tenant_id),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "version": str(version),
        }

    @staticmethod
    def _content_context(
        *,
        key_id: uuid.UUID,
        tenant_id: uuid.UUID,
        resource_type: str,
        resource_id: str,
        purpose: str,
        record_id: str,
    ) -> dict[str, str]:
        return {
            "content_key_id": str(key_id),
            "tenant_id": str(tenant_id),
            "resource_type": resource_type,
            "resource_id": resource_id,
            "purpose": purpose,
            "record_id": record_id,
            "schema_version": "1",
        }

    async def _resource_key(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        resource_type: str,
        resource_id: str,
    ) -> tuple[uuid.UUID, bytes]:
        # A streaming writer can persist hundreds of ordered events through
        # one AsyncSession. Re-querying content_encryption_keys for every frame
        # would turn encryption into a database-latency tax. Cache only the
        # key identity in the current Session; a new request/transaction still
        # revalidates the durable active row, while the existing bounded DEK
        # cache avoids repeated unwrap work.
        session_cache = session.info.setdefault(
            "vibecanvas_content_resource_keys",
            {},
        )
        resource_cache_key = (str(tenant_id), resource_type, resource_id)
        cached_key_id = session_cache.get(resource_cache_key)
        if cached_key_id is not None:
            cached_dek = self._dek_cache.get(cached_key_id)
            if cached_dek is not None:
                return cached_key_id, cached_dek
        row = (
            await session.execute(
                select(ContentEncryptionKey).where(
                    ContentEncryptionKey.tenant_id == tenant_id,
                    ContentEncryptionKey.resource_type == resource_type,
                    ContentEncryptionKey.resource_id == resource_id,
                    ContentEncryptionKey.status == "active",
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            dek = await self._unwrap(row)
            session_cache[resource_cache_key] = row.key_id
            return row.key_id, dek

        key_id = uuid.uuid4()
        version = 1
        context = self._key_context(
            key_id=key_id,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            version=version,
        )
        dek = bytearray(os.urandom(AES_KEY_BYTES))
        try:
            wrapped = await self._provider.wrap_key(bytes(dek), context=context)
            row = ContentEncryptionKey(
                key_id=key_id,
                tenant_id=tenant_id,
                resource_type=resource_type,
                resource_id=resource_id,
                version=version,
                status="active",
                algorithm="AES-256-GCM",
                wrapped_dek=base64.b64encode(wrapped.ciphertext).decode("ascii"),
                wrapping_key_id=wrapped.key_id,
                wrapping_key_version=wrapped.key_version,
                context_hash=hashlib.sha256(
                    canonical_context(context)
                ).hexdigest(),
            )
            session.add(row)
            await session.flush()
            value = bytes(dek)
            self._dek_cache.put(key_id, value)
            session_cache[resource_cache_key] = key_id
            return key_id, value
        finally:
            wipe_bytes(dek)

    async def _unwrap(self, row: ContentEncryptionKey) -> bytes:
        cached = self._dek_cache.get(row.key_id)
        if cached is not None:
            return cached
        context = self._key_context(
            key_id=row.key_id,
            tenant_id=row.tenant_id,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            version=row.version,
        )
        if (
            hashlib.sha256(canonical_context(context)).hexdigest()
            != row.context_hash
        ):
            raise SecretIntegrityError("content key context does not match")
        dek = await self._provider.unwrap_key(
            WrappedKey(
                ciphertext=base64.b64decode(row.wrapped_dek),
                key_id=row.wrapping_key_id,
                key_version=row.wrapping_key_version,
            ),
            context=context,
        )
        if len(dek) != AES_KEY_BYTES:
            raise SecretUnavailableError("content key has an invalid length")
        self._dek_cache.put(row.key_id, dek)
        return bytes(dek)

    async def encrypt_json(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID | str,
        resource_type: str,
        resource_id: str,
        purpose: str,
        record_id: str,
        value: Any,
    ) -> ContentCiphertext:
        plaintext = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return await self.encrypt_bytes(
            session,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=purpose,
            record_id=record_id,
            plaintext=plaintext,
        )

    async def encrypt_bytes(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID | str,
        resource_type: str,
        resource_id: str,
        purpose: str,
        record_id: str,
        plaintext: bytes,
    ) -> ContentCiphertext:
        """Encrypt opaque bytes without interpreting Runtime serialization.

        Checkpoint payloads are intentionally serialized in the sandbox.  This
        byte-oriented entry point lets the host protect them at rest without
        deserializing sandbox-controlled data or changing the Runtime protocol.
        """
        tenant_uuid = uuid.UUID(str(tenant_id))
        key_id, dek = await self._resource_key(
            session,
            tenant_id=tenant_uuid,
            resource_type=resource_type,
            resource_id=resource_id,
        )
        context = self._content_context(
            key_id=key_id,
            tenant_id=tenant_uuid,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=purpose,
            record_id=record_id,
        )
        nonce = os.urandom(GCM_NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(
            nonce,
            bytes(plaintext),
            canonical_context(context),
        )
        return ContentCiphertext(
            key_id=key_id,
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            nonce=base64.b64encode(nonce).decode("ascii"),
        )

    async def decrypt_json(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        resource_type: str,
        resource_id: str,
        purpose: str,
        record_id: str,
        ciphertext: str,
        nonce: str,
    ) -> Any:
        plaintext = await self.decrypt_bytes(
            session,
            key_id=key_id,
            tenant_id=tenant_id,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=purpose,
            record_id=record_id,
            ciphertext=ciphertext,
            nonce=nonce,
        )
        try:
            return json.loads(plaintext)
        except Exception as exc:
            raise SecretIntegrityError(
                "content ciphertext did not contain valid JSON"
            ) from exc

    async def decrypt_bytes(
        self,
        session: AsyncSession,
        *,
        key_id: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        resource_type: str,
        resource_id: str,
        purpose: str,
        record_id: str,
        ciphertext: str,
        nonce: str,
    ) -> bytes:
        """Decrypt opaque bytes and verify their resource-bound AAD."""
        tenant_uuid = uuid.UUID(str(tenant_id))
        key_uuid = uuid.UUID(str(key_id))
        # Ordered replay ledgers commonly decrypt hundreds of records from one
        # Chat in a single request. Once this Session has verified that the key
        # belongs to the requested tenant/resource, reuse the bounded in-memory
        # DEK cache instead of issuing one content_encryption_keys query per
        # event. A new request uses a new Session and therefore revalidates the
        # durable key row; no authorization decision is cached here.
        session_cache = session.info.setdefault(
            "vibecanvas_content_decryption_keys",
            {},
        )
        cache_key = (
            str(key_uuid),
            str(tenant_uuid),
            resource_type,
            resource_id,
        )
        dek = None
        if session_cache.get(cache_key) is True:
            dek = self._dek_cache.get(key_uuid)
        if dek is None:
            row = (
                await session.execute(
                    select(ContentEncryptionKey).where(
                        ContentEncryptionKey.key_id == key_uuid,
                        ContentEncryptionKey.tenant_id == tenant_uuid,
                        ContentEncryptionKey.resource_type == resource_type,
                        ContentEncryptionKey.resource_id == resource_id,
                        ContentEncryptionKey.status.in_(("active", "retired")),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise SecretUnavailableError("content key is unavailable")
            dek = await self._unwrap(row)
            session_cache[cache_key] = True
        context = self._content_context(
            key_id=key_uuid,
            tenant_id=tenant_uuid,
            resource_type=resource_type,
            resource_id=resource_id,
            purpose=purpose,
            record_id=record_id,
        )
        try:
            return AESGCM(dek).decrypt(
                base64.b64decode(nonce),
                base64.b64decode(ciphertext),
                canonical_context(context),
            )
        except (SecretUnavailableError, SecretIntegrityError):
            raise
        except Exception as exc:
            raise SecretIntegrityError(
                "content ciphertext failed integrity check"
            ) from exc


@lru_cache(maxsize=1)
def content_encryption_service() -> ContentEncryptionService:
    return ContentEncryptionService()
