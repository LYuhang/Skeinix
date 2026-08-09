"""Envelope protection for VFS display metadata.

VFS bodies already live in the encrypted Object Store.  ``abstract`` is a
small, user-visible summary, but it can contain search terms, browser titles,
commands, paths, or model-produced text.  Treat it as private content instead
of trying to maintain a second, lossy redaction policy.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.security.crypto_core import SecretIntegrityError


_KINDS = {
    "artifact": ("vfs_scope", "vfs_artifact_abstract"),
    "scratch": ("vfs_scope", "vfs_scratch_abstract"),
    "run": ("vfs_run", "vfs_run_abstract"),
}


def _coordinates(kind: str) -> tuple[str, str]:
    try:
        return _KINDS[kind]
    except KeyError as exc:
        raise ValueError("unsupported VFS abstract kind") from exc


async def protect_vfs_abstract(
    session: AsyncSession,
    *,
    tenant_id: str,
    kind: str,
    resource_id: str,
    path: str,
    abstract: str | None,
) -> dict[str, Any]:
    """Return ciphertext-only ORM values for one VFS abstract.

    Empty summaries need no ciphertext.  Non-empty summaries reuse the normal
    per-resource content DEK, so a directory listing does not create one KMS
    operation per row.
    """
    value = str(abstract or "")
    if not value:
        return {
            "abstract": "",
            "abstract_ciphertext": None,
            "abstract_nonce": None,
            "abstract_key_id": None,
        }
    resource_type, purpose = _coordinates(kind)
    encrypted = await content_encryption_service().encrypt_json(
        session,
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=str(resource_id),
        purpose=purpose,
        record_id=str(path),
        value=value,
    )
    return {
        "abstract": "",
        "abstract_ciphertext": encrypted.ciphertext,
        "abstract_nonce": encrypted.nonce,
        "abstract_key_id": encrypted.key_id,
    }


async def unprotect_vfs_abstract(
    session: AsyncSession,
    *,
    tenant_id: str,
    kind: str,
    resource_id: str,
    path: str,
    abstract: str | None,
    ciphertext: str | None,
    nonce: str | None,
    key_id: object | None,
) -> str:
    """Decrypt one VFS abstract and reject plaintext/incomplete storage."""
    if abstract:
        raise SecretIntegrityError("VFS abstract plaintext is forbidden")
    present = (ciphertext is not None, nonce is not None, key_id is not None)
    if not any(present):
        return ""
    if not all(present) or not ciphertext or not nonce:
        raise SecretIntegrityError("VFS abstract ciphertext is incomplete")
    resource_type, purpose = _coordinates(kind)
    value = await content_encryption_service().decrypt_json(
        session,
        key_id=key_id,
        tenant_id=tenant_id,
        resource_type=resource_type,
        resource_id=str(resource_id),
        purpose=purpose,
        record_id=str(path),
        ciphertext=ciphertext,
        nonce=nonce,
    )
    if not isinstance(value, str):
        raise SecretIntegrityError("VFS abstract ciphertext has invalid content")
    return value
