"""Decryption helper used only by the one-time strict upgrade command."""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


def _migration_fernet() -> Fernet:
    key = os.environ.get("OAUTH_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("OAUTH_ENCRYPTION_KEY is required for strict upgrade")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise RuntimeError("OAUTH_ENCRYPTION_KEY is invalid") from exc


def encrypt_migration_fixture(value: str | None) -> str | None:
    """Build old-format ciphertext only for migration fixture coverage."""
    if value is None:
        return None
    return _migration_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_migration_value(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _migration_fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("old OAuth ciphertext cannot be migrated") from exc
