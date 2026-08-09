"""Shared ciphertext-only persistence helpers for MFA factors."""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.storage.models import UserMfaTotp


async def decrypt_totp_secret(
    session: AsyncSession,
    row: UserMfaTotp,
) -> str:
    value = await content_encryption_service().decrypt_json(
        session,
        key_id=row.secret_key_id,
        tenant_id=row.tenant_id,
        resource_type="user_identity",
        resource_id=str(row.user_id),
        purpose="mfa_totp_seed",
        record_id=str(row.user_id),
        ciphertext=row.secret_ciphertext,
        nonce=row.secret_nonce,
    )
    if not isinstance(value, dict) or not isinstance(value.get("secret"), str):
        raise HTTPException(503, "mfa_factor_unavailable")
    return value["secret"]
