"""Shared cryptographic primitives and key-wrapping providers.

This module is the single low-level cryptography boundary for secrets,
structured private content, identity lookup tokens, and local Object Store
containers.  Storage-specific services still own their schemas and lifecycle,
but they do not independently choose algorithms, key sizes, KMS providers,
canonical AAD encoding, cache semantics, or key wiping behavior.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import threading
import time
from typing import Hashable, Mapping, Protocol
import unicodedata

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from vibecanvas_api.config import config


AES_KEY_BYTES = 32
GCM_NONCE_BYTES = 12
GCM_TAG_BYTES = 16


class SecretServiceError(RuntimeError):
    """Base crypto error whose message never contains protected material."""


class SecretUnavailableError(SecretServiceError):
    pass


class SecretIntegrityError(SecretServiceError):
    pass


@dataclass(frozen=True, slots=True)
class WrappedKey:
    ciphertext: bytes
    key_id: str
    key_version: str


class WrappingKeyProvider(Protocol):
    async def wrap_key(
        self,
        plaintext_dek: bytes,
        *,
        context: Mapping[str, str],
    ) -> WrappedKey: ...

    async def unwrap_key(
        self,
        wrapped: WrappedKey,
        *,
        context: Mapping[str, str],
    ) -> bytes: ...


def canonical_context(context: Mapping[str, str]) -> bytes:
    """Encode non-secret AAD identically across every envelope service."""
    return json.dumps(
        dict(sorted(context.items())),
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


def wipe_bytes(value: bytearray) -> None:
    """Best-effort overwrite for mutable in-process key material."""
    for index in range(len(value)):
        value[index] = 0


class DekCache:
    """Small TTL/LRU cache shared by envelope services.

    The cache stores mutable copies so eviction and replacement can overwrite
    key bytes.  A lock makes the process singleton safe for async services that
    are also reached through short-lived worker threads.
    """

    def __init__(self, *, ttl_seconds: float = 300.0, max_entries: int = 256):
        self._ttl = max(1.0, float(ttl_seconds))
        self._max_entries = max(1, int(max_entries))
        self._values: OrderedDict[
            Hashable, tuple[float, bytearray]
        ] = OrderedDict()
        self._lock = threading.RLock()

    def put(self, cache_key: Hashable, dek: bytes) -> None:
        if len(dek) != AES_KEY_BYTES:
            raise SecretUnavailableError("data encryption key has invalid length")
        with self._lock:
            previous = self._values.pop(cache_key, None)
            if previous is not None:
                wipe_bytes(previous[1])
            self._values[cache_key] = (
                time.monotonic() + self._ttl,
                bytearray(dek),
            )
            while len(self._values) > self._max_entries:
                _, (_, evicted) = self._values.popitem(last=False)
                wipe_bytes(evicted)

    def get(self, cache_key: Hashable) -> bytes | None:
        with self._lock:
            cached = self._values.pop(cache_key, None)
            if cached is None:
                return None
            expires_at, dek = cached
            if expires_at <= time.monotonic():
                wipe_bytes(dek)
                return None
            self._values[cache_key] = cached
            return bytes(dek)

    def discard(self, cache_key: Hashable) -> None:
        with self._lock:
            cached = self._values.pop(cache_key, None)
            if cached is not None:
                wipe_bytes(cached[1])

    def clear(self) -> None:
        with self._lock:
            for _, dek in self._values.values():
                wipe_bytes(dek)
            self._values.clear()


def keyed_lookup_digest(
    *,
    domain: str,
    components: tuple[str, ...],
    value: str,
    casefold: bool = False,
) -> str:
    """Return a stable HMAC equality token without reversible encryption."""
    key = config.content_lookup_hmac_key
    if not key:
        raise SecretUnavailableError("content lookup HMAC key is unavailable")
    normalized = unicodedata.normalize("NFC", str(value).strip())
    if casefold:
        normalized = normalized.casefold()
    message = "\0".join((domain, *components, normalized)).encode("utf-8")
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


class LocalWrappingKeyProvider:
    """Development-only wrapping provider backed by a host file/env key."""

    def __init__(self, master_key: bytes, *, key_id: str = "local-development"):
        if len(master_key) != AES_KEY_BYTES:
            raise SecretUnavailableError("local KMS master key must be 32 bytes")
        self._master_key = bytes(master_key)
        self._key_id = key_id
        self._key_version = hashlib.sha256(master_key).hexdigest()[:16]

    async def wrap_key(
        self,
        plaintext_dek: bytes,
        *,
        context: Mapping[str, str],
    ) -> WrappedKey:
        nonce = os.urandom(GCM_NONCE_BYTES)
        wrapped = AESGCM(self._master_key).encrypt(
            nonce,
            plaintext_dek,
            canonical_context(context),
        )
        return WrappedKey(
            ciphertext=nonce + wrapped,
            key_id=self._key_id,
            key_version=self._key_version,
        )

    async def unwrap_key(
        self,
        wrapped: WrappedKey,
        *,
        context: Mapping[str, str],
    ) -> bytes:
        if wrapped.key_version != self._key_version:
            raise SecretUnavailableError("local KMS key version is unavailable")
        try:
            return AESGCM(self._master_key).decrypt(
                wrapped.ciphertext[:GCM_NONCE_BYTES],
                wrapped.ciphertext[GCM_NONCE_BYTES:],
                canonical_context(context),
            )
        except Exception as exc:
            raise SecretIntegrityError(
                "wrapped data key failed integrity check"
            ) from exc


class AwsKmsWrappingKeyProvider:
    """AWS KMS provider using the process/pod workload identity chain."""

    def __init__(self, key_id: str):
        if not key_id:
            raise SecretUnavailableError("AWS KMS key id is not configured")
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - packaging gate
            raise SecretUnavailableError("AWS KMS client is not installed") from exc
        self._key_id = key_id
        self._client = boto3.client("kms")

    async def wrap_key(
        self,
        plaintext_dek: bytes,
        *,
        context: Mapping[str, str],
    ) -> WrappedKey:
        try:
            response = await asyncio.to_thread(
                self._client.encrypt,
                KeyId=self._key_id,
                Plaintext=plaintext_dek,
                EncryptionContext=dict(context),
            )
        except Exception as exc:
            raise SecretUnavailableError("KMS wrap operation failed") from exc
        resolved_key = str(response.get("KeyId") or self._key_id)
        return WrappedKey(
            ciphertext=bytes(response["CiphertextBlob"]),
            key_id=resolved_key,
            key_version=resolved_key,
        )

    async def unwrap_key(
        self,
        wrapped: WrappedKey,
        *,
        context: Mapping[str, str],
    ) -> bytes:
        try:
            response = await asyncio.to_thread(
                self._client.decrypt,
                KeyId=wrapped.key_id,
                CiphertextBlob=wrapped.ciphertext,
                EncryptionContext=dict(context),
            )
            return bytes(response["Plaintext"])
        except Exception as exc:
            raise SecretUnavailableError("KMS unwrap operation failed") from exc


_ephemeral_development_key = os.urandom(AES_KEY_BYTES)


def _decode_local_key(value: str) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except Exception as exc:
        raise SecretUnavailableError("local KMS key is not valid base64") from exc
    if len(decoded) != AES_KEY_BYTES:
        raise SecretUnavailableError("local KMS key must decode to 32 bytes")
    return decoded


def local_master_key_from_config(*, require_persistent: bool = False) -> bytes:
    """Resolve the host-only local wrapping key for non-production backends."""
    if config.kms_local_master_key:
        return _decode_local_key(config.kms_local_master_key)
    if config.kms_local_master_key_file:
        path = Path(config.kms_local_master_key_file)
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode & 0o077:
                raise SecretUnavailableError(
                    "local KMS key file must not be group/world accessible"
                )
            return _decode_local_key(path.read_text(encoding="ascii").strip())
        except SecretUnavailableError:
            raise
        except OSError as exc:
            raise SecretUnavailableError(
                "local KMS key file is unavailable"
            ) from exc
    if config.environment in {"development", "test"} and not require_persistent:
        return _ephemeral_development_key
    raise SecretUnavailableError("persistent local KMS key is not configured")


def wrapping_provider_from_config() -> WrappingKeyProvider:
    provider = config.kms_provider.lower() or (
        "local" if config.environment in {"development", "test"} else ""
    )
    if provider == "local":
        return LocalWrappingKeyProvider(
            local_master_key_from_config(),
            key_id=config.kms_key_id or "local-development",
        )
    if provider == "aws-kms":
        return AwsKmsWrappingKeyProvider(config.kms_key_id)
    raise SecretUnavailableError("configured KMS provider is unsupported")
