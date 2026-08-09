"""Opaque token generation + sha256 hashing.

Session / reset tokens are high-entropy random strings. The DB stores
ONLY sha256(token); the raw token lives only in the client. sha256 is
fine here (unlike passwords — those are low-entropy and need argon2)."""
import hashlib
import secrets


def new_token() -> tuple[str, str]:
    """Return (raw_token, sha256_hex)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
