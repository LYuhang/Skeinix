"""Encryption and keyed lookup helpers for enterprise directory records."""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import (
    ContentCiphertext,
    content_encryption_service,
)
from vibecanvas_api.security.crypto_core import (
    SecretIntegrityError,
    keyed_lookup_digest,
)
from vibecanvas_api.storage.models_enterprise_identity import (
    EnterpriseDirectoryUser,
)


@dataclass(frozen=True, slots=True)
class DirectoryUserPrivate:
    external_id: str
    user_name: str


def directory_lookup_digest(
    provider_id: uuid.UUID | str,
    attribute: str,
    value: str,
    *,
    casefold: bool = False,
) -> str:
    return keyed_lookup_digest(
        domain="vibecanvas:enterprise-directory:v1",
        components=(str(provider_id), attribute),
        value=value.strip(),
        casefold=casefold,
    )


async def encrypt_directory_user_private(
    session: AsyncSession,
    *,
    directory_user_id: uuid.UUID,
    provider_id: uuid.UUID,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    external_id: str,
    user_name: str,
) -> ContentCiphertext:
    return await content_encryption_service().encrypt_json(
        session,
        tenant_id=tenant_id,
        resource_type="enterprise_directory_user",
        resource_id=str(directory_user_id),
        purpose="directory_identity",
        record_id=str(directory_user_id),
        value={
            "provider_id": str(provider_id),
            "user_id": str(user_id),
            "external_id": external_id.strip(),
            "user_name": user_name.strip(),
        },
    )


async def decrypt_directory_user_private(
    session: AsyncSession,
    row: EnterpriseDirectoryUser,
) -> DirectoryUserPrivate:
    value = await content_encryption_service().decrypt_json(
        session,
        key_id=row.private_key_id,
        tenant_id=row.tenant_id,
        resource_type="enterprise_directory_user",
        resource_id=str(row.directory_user_id),
        purpose="directory_identity",
        record_id=str(row.directory_user_id),
        ciphertext=row.private_ciphertext,
        nonce=row.private_nonce,
    )
    if not isinstance(value, dict):
        raise SecretIntegrityError("enterprise directory payload is invalid")
    if (
        value.get("provider_id") != str(row.provider_id)
        or value.get("user_id") != str(row.user_id)
        or not isinstance(value.get("external_id"), str)
        or not isinstance(value.get("user_name"), str)
    ):
        raise SecretIntegrityError("enterprise directory payload is invalid")
    return DirectoryUserPrivate(
        external_id=value["external_id"],
        user_name=value["user_name"],
    )


__all__ = [
    "DirectoryUserPrivate",
    "decrypt_directory_user_private",
    "directory_lookup_digest",
    "encrypt_directory_user_private",
]
