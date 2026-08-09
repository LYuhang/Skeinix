"""Host-side envelope encryption for user and platform secrets.

Each record receives a random 256-bit data-encryption key (DEK). The plaintext
is encrypted with AES-256-GCM and context-bound AAD; only the DEK is sent to the
configured KMS wrapping key. Business tables retain the opaque ``secret_ref``.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from functools import lru_cache
import hashlib
import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.audit import actions as audit_actions
from vibecanvas_api.audit.service import record_audit
from vibecanvas_api.security.crypto_core import (
    AES_KEY_BYTES,
    GCM_NONCE_BYTES,
    AwsKmsWrappingKeyProvider,
    DekCache,
    LocalWrappingKeyProvider,
    SecretIntegrityError,
    SecretServiceError,
    SecretUnavailableError,
    WrappedKey,
    WrappingKeyProvider,
    canonical_context,
    local_master_key_from_config,
    wipe_bytes,
    wrapping_provider_from_config,
)
from vibecanvas_api.storage.models_secrets import EncryptedSecret


__all__ = [
    "AwsKmsWrappingKeyProvider",
    "LocalWrappingKeyProvider",
    "SecretIntegrityError",
    "SecretService",
    "SecretServiceError",
    "SecretUnavailableError",
    "WrappedKey",
    "WrappingKeyProvider",
    "local_master_key_from_config",
    "secret_service",
    "suppress_secret_creation_audit",
    "wrapping_provider_from_config",
]


_emit_creation_audit: ContextVar[bool] = ContextVar(
    "secret_service_emit_creation_audit",
    default=True,
)


@contextmanager
def suppress_secret_creation_audit():
    """Suppress per-row audit events during the pre-schema secret backfill.

    Normal application writes always retain their audit event.  The strict
    deployment migrator, however, moves legacy values while the database is
    intentionally paused before the later encrypted-audit schema exists.
    Keeping this override context-local avoids both future-schema ORM writes
    and a process-wide mode that could accidentally disable runtime auditing.
    """
    token = _emit_creation_audit.set(False)
    try:
        yield
    finally:
        _emit_creation_audit.reset(token)


class SecretService:
    def __init__(
        self,
        provider: WrappingKeyProvider | None = None,
        *,
        cache_ttl_seconds: float = 300.0,
        cache_size: int = 256,
    ):
        self._provider = provider or wrapping_provider_from_config()
        self._dek_cache = DekCache(
            ttl_seconds=cache_ttl_seconds,
            max_entries=cache_size,
        )

    @staticmethod
    def _cache_key(row: EncryptedSecret) -> tuple[object, ...]:
        """Bind cached material to the durable wrapped-key revision."""
        wrapped_fingerprint = hashlib.sha256(
            (row.wrapped_dek or "").encode("ascii")
        ).digest()
        return (
            row.secret_id,
            row.context_hash,
            row.wrapping_key_id,
            row.wrapping_key_version,
            wrapped_fingerprint,
        )

    @staticmethod
    def _context(
        *,
        secret_id: uuid.UUID,
        tenant_id: uuid.UUID,
        purpose: str,
        resource_type: str,
        resource_id: str,
        version: int,
    ) -> dict[str, str]:
        return {
            "secret_id": str(secret_id),
            "tenant_id": str(tenant_id),
            "purpose": purpose,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "version": str(version),
        }

    async def put_text(
        self,
        session: AsyncSession,
        *,
        tenant_id: uuid.UUID | str,
        purpose: str,
        resource_type: str,
        resource_id: uuid.UUID | str,
        plaintext: str,
        version: int = 1,
    ) -> uuid.UUID:
        if not plaintext:
            raise ValueError("secret plaintext must not be empty")
        tenant_uuid = uuid.UUID(str(tenant_id))
        secret_id = uuid.uuid4()
        resource_value = str(resource_id)
        context = self._context(
            secret_id=secret_id,
            tenant_id=tenant_uuid,
            purpose=purpose,
            resource_type=resource_type,
            resource_id=resource_value,
            version=version,
        )
        aad = canonical_context(context)
        dek = bytearray(os.urandom(AES_KEY_BYTES))
        nonce = os.urandom(GCM_NONCE_BYTES)
        try:
            ciphertext = AESGCM(bytes(dek)).encrypt(
                nonce,
                plaintext.encode("utf-8"),
                aad,
            )
            wrapped = await self._provider.wrap_key(bytes(dek), context=context)
            row = EncryptedSecret(
                secret_id=secret_id,
                tenant_id=tenant_uuid,
                purpose=purpose,
                resource_type=resource_type,
                resource_id=resource_value,
                version=version,
                status="active",
                algorithm="AES-256-GCM",
                ciphertext=base64.b64encode(ciphertext).decode("ascii"),
                nonce=base64.b64encode(nonce).decode("ascii"),
                wrapped_dek=base64.b64encode(wrapped.ciphertext).decode("ascii"),
                wrapping_key_id=wrapped.key_id,
                wrapping_key_version=wrapped.key_version,
                context_hash=hashlib.sha256(aad).hexdigest(),
            )
            session.add(row)
            if _emit_creation_audit.get():
                await record_audit(
                    session,
                    action=audit_actions.SECRET_CREATE,
                    actor_user_id=None,
                    actor_email=None,
                    target_type=audit_actions.TARGET_SECRET,
                    target_id=str(secret_id),
                    target_name=purpose,
                    outcome="success",
                    meta={
                        "resource_type": resource_type,
                        "resource_id": resource_value,
                        "version": version,
                        "wrapping_key_id": wrapped.key_id,
                        "wrapping_key_version": wrapped.key_version,
                    },
                )
            await session.flush()
            self._dek_cache.put(self._cache_key(row), bytes(dek))
            return secret_id
        finally:
            wipe_bytes(dek)

    async def resolve_text(
        self,
        session: AsyncSession,
        *,
        secret_ref: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
        purpose: str,
        resource_type: str,
        resource_id: uuid.UUID | str,
    ) -> str:
        tenant_uuid = uuid.UUID(str(tenant_id))
        row = (
            await session.execute(
                select(EncryptedSecret).where(
                    EncryptedSecret.secret_id == uuid.UUID(str(secret_ref)),
                    EncryptedSecret.tenant_id == tenant_uuid,
                    EncryptedSecret.purpose == purpose,
                    EncryptedSecret.resource_type == resource_type,
                    EncryptedSecret.resource_id == str(resource_id),
                    EncryptedSecret.status == "active",
                )
            )
        ).scalar_one_or_none()
        if row is None or not row.ciphertext or not row.nonce or not row.wrapped_dek:
            raise SecretUnavailableError("secret reference is unavailable")
        context = self._context(
            secret_id=row.secret_id,
            tenant_id=row.tenant_id,
            purpose=row.purpose,
            resource_type=row.resource_type,
            resource_id=row.resource_id,
            version=row.version,
        )
        aad = canonical_context(context)
        if not hashlib.sha256(aad).hexdigest() == row.context_hash:
            raise SecretIntegrityError("secret encryption context does not match")
        wrapped = WrappedKey(
            ciphertext=base64.b64decode(row.wrapped_dek),
            key_id=row.wrapping_key_id,
            key_version=row.wrapping_key_version,
        )
        cache_key = self._cache_key(row)
        cached_dek = self._dek_cache.get(cache_key)
        dek = bytearray(
            cached_dek
            if cached_dek is not None
            else await self._provider.unwrap_key(wrapped, context=context)
        )
        try:
            plaintext = AESGCM(bytes(dek)).decrypt(
                base64.b64decode(row.nonce),
                base64.b64decode(row.ciphertext),
                aad,
            )
            value = plaintext.decode("utf-8")
            if cached_dek is None:
                self._dek_cache.put(cache_key, bytes(dek))
            return value
        except SecretServiceError:
            raise
        except Exception as exc:
            raise SecretIntegrityError("secret ciphertext failed integrity check") from exc
        finally:
            wipe_bytes(dek)

    async def destroy(
        self,
        session: AsyncSession,
        *,
        secret_ref: uuid.UUID | str,
        tenant_id: uuid.UUID | str,
    ) -> None:
        secret_id = uuid.UUID(str(secret_ref))
        await session.execute(
            update(EncryptedSecret)
            .where(
                EncryptedSecret.secret_id == secret_id,
                EncryptedSecret.tenant_id == uuid.UUID(str(tenant_id)),
            )
            .values(
                status="destroyed",
                ciphertext=None,
                nonce=None,
                wrapped_dek=None,
                destroyed_at=func.now(),
                updated_at=func.now(),
            )
        )
        # Destroy is rare and security-sensitive. Clearing the small bounded
        # cache avoids retaining any wrapped-key revision for this secret
        # without adding a second cache index solely for targeted eviction.
        self._dek_cache.clear()
        await record_audit(
            session,
            action=audit_actions.SECRET_DESTROY,
            actor_user_id=None,
            actor_email=None,
            target_type=audit_actions.TARGET_SECRET,
            target_id=str(secret_id),
            target_name=None,
            outcome="success",
            meta={},
        )
        await session.flush()


@lru_cache(maxsize=1)
def secret_service() -> SecretService:
    """Create a lightweight service; provider clients manage their own pools."""
    return SecretService()
