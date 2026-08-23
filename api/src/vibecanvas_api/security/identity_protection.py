"""Identity PII adapter over the shared envelope-encryption boundary.

Identity lookups happen before an organization is known.  Equality therefore
uses a global, domain-separated keyed digest; reversible display values remain
random-nonce ciphertext under the user's home-tenant content key.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import (
    ContentCiphertext,
    content_encryption_service,
)
from vibecanvas_api.security.crypto_core import (
    SecretIntegrityError,
    keyed_lookup_digest,
)


class ProtectedUserRow(Protocol):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    profile_key_id: uuid.UUID | None
    profile_ciphertext: str | None
    profile_nonce: str | None


@dataclass(frozen=True, slots=True)
class IdentityProfile:
    email: str
    display_name: str


def identity_lookup_digest(provider: str, provider_uid: str) -> str:
    normalized_provider = provider.strip().lower()
    return keyed_lookup_digest(
        domain="vibecanvas:identity-lookup:v1",
        components=(normalized_provider,),
        value=provider_uid,
        casefold=normalized_provider == "password",
    )


def profile_email_lookup_digest(email: str) -> str:
    """Return the global exact-email lookup used by explicit sharing search.

    Password identities already use this domain, so existing password users
    can be backfilled without decrypting their profiles. The value remains a
    keyed, case-insensitive digest and is never returned to clients.
    """
    return identity_lookup_digest("password", email)


async def encrypt_user_profile(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    email: str,
    display_name: str,
) -> ContentCiphertext:
    return await content_encryption_service().encrypt_json(
        session,
        tenant_id=tenant_id,
        resource_type="user_identity",
        resource_id=str(user_id),
        purpose="identity_profile",
        record_id=str(user_id),
        value={"email": email.strip(), "display_name": display_name},
    )


async def encrypt_provider_uid(
    session: AsyncSession,
    *,
    identity_id: uuid.UUID,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    provider: str,
    provider_uid: str,
) -> ContentCiphertext:
    return await content_encryption_service().encrypt_json(
        session,
        tenant_id=tenant_id,
        resource_type="user_identity",
        resource_id=str(user_id),
        purpose="identity_provider_uid",
        record_id=str(identity_id),
        value={"provider": provider.strip().lower(), "provider_uid": provider_uid},
    )


async def encrypt_account_deletion_email(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    email: str,
) -> ContentCiphertext:
    previous_tenant = (
        await session.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        )
    ).scalar_one_or_none() or ""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    try:
        return await content_encryption_service().encrypt_json(
            session,
            tenant_id=tenant_id,
            resource_type="user_identity",
            resource_id=str(user_id),
            purpose="account_deletion_email",
            record_id=str(user_id),
            value={"email": email.strip()},
        )
    finally:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": previous_tenant},
        )


async def decrypt_user_profile(
    session: AsyncSession,
    row: ProtectedUserRow,
) -> IdentityProfile:
    fields = (row.profile_key_id, row.profile_ciphertext, row.profile_nonce)
    if all(value is None for value in fields):
        # Structural test/bootstrap users may deliberately carry no PII and no
        # auth identity. They cannot authenticate and expose only empty values.
        return IdentityProfile(email="", display_name="")
    if any(value is None for value in fields):
        raise SecretIntegrityError("identity profile encryption is incomplete")

    previous_tenant = (
        await session.execute(
            text("SELECT current_setting('app.tenant_id', true)")
        )
    ).scalar_one_or_none() or ""
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(row.tenant_id)},
    )
    try:
        value = await content_encryption_service().decrypt_json(
            session,
            key_id=row.profile_key_id,
            tenant_id=row.tenant_id,
            resource_type="user_identity",
            resource_id=str(row.user_id),
            purpose="identity_profile",
            record_id=str(row.user_id),
            ciphertext=str(row.profile_ciphertext),
            nonce=str(row.profile_nonce),
        )
    finally:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": previous_tenant},
        )
    if not isinstance(value, dict):
        raise SecretIntegrityError("identity profile payload is invalid")
    email = value.get("email")
    display_name = value.get("display_name")
    if not isinstance(email, str) or not isinstance(display_name, str):
        raise SecretIntegrityError("identity profile payload is invalid")
    return IdentityProfile(email=email, display_name=display_name)
