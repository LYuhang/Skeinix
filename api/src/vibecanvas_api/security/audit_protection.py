"""Encrypted private payloads for the append-only audit ledger."""
from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import (
    ContentCiphertext,
    content_encryption_service,
)
from vibecanvas_api.security.crypto_core import keyed_lookup_digest


@dataclass(frozen=True, slots=True)
class AuditPrivatePayload:
    actor_email: str | None
    target_name: str | None
    ip_address: str | None
    user_agent: str | None
    meta: dict


def audit_lookup_digest(kind: str, value: str | None) -> str | None:
    if not value:
        return None
    return keyed_lookup_digest(
        domain="vibecanvas:audit-lookup:v1",
        components=(kind,),
        value=value,
        casefold=kind == "actor_email",
    )


async def encrypt_audit_payload(
    session: AsyncSession,
    *,
    audit_id: uuid.UUID,
    tenant_id: uuid.UUID,
    actor_email: str | None,
    target_name: str | None,
    ip_address: str | None,
    user_agent: str | None,
    meta: dict,
) -> ContentCiphertext:
    return await content_encryption_service().encrypt_json(
        session,
        tenant_id=tenant_id,
        resource_type="organization_audit",
        resource_id=str(tenant_id),
        purpose="audit_private",
        record_id=str(audit_id),
        value={
            "actor_email": actor_email,
            "target_name": target_name,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "meta": meta,
        },
    )


async def decrypt_audit_payload(
    session: AsyncSession,
    *,
    audit_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    key_id: uuid.UUID | None,
    ciphertext: str | None,
    nonce: str | None,
) -> AuditPrivatePayload:
    fields = (key_id, ciphertext, nonce)
    if all(value is None for value in fields):
        return AuditPrivatePayload(None, None, None, None, {})
    if tenant_id is None or any(value is None for value in fields):
        from vibecanvas_api.security.crypto_core import SecretIntegrityError

        raise SecretIntegrityError("audit private encryption is incomplete")
    value = await content_encryption_service().decrypt_json(
        session,
        key_id=key_id,
        tenant_id=tenant_id,
        resource_type="organization_audit",
        resource_id=str(tenant_id),
        purpose="audit_private",
        record_id=str(audit_id),
        ciphertext=str(ciphertext),
        nonce=str(nonce),
    )
    if not isinstance(value, dict) or not isinstance(value.get("meta"), dict):
        from vibecanvas_api.security.crypto_core import SecretIntegrityError

        raise SecretIntegrityError("audit private payload is invalid")
    optional = ("actor_email", "target_name", "ip_address", "user_agent")
    if any(value.get(field) is not None and not isinstance(value[field], str)
           for field in optional):
        from vibecanvas_api.security.crypto_core import SecretIntegrityError

        raise SecretIntegrityError("audit private payload is invalid")
    return AuditPrivatePayload(
        actor_email=value.get("actor_email"),
        target_name=value.get("target_name"),
        ip_address=value.get("ip_address"),
        user_agent=value.get("user_agent"),
        meta=value["meta"],
    )
