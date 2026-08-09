"""RFC 6238 TOTP primitives and one-time recovery-code hashing.

The TOTP seed is encrypted by the shared ContentEncryptionService at the
storage boundary.  This module intentionally contains no persistence or HTTP
logic so the cryptographic/replay behavior is easy to test exhaustively.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from vibecanvas_api.security.crypto_core import keyed_lookup_digest


TOTP_PERIOD_SECONDS = 30
TOTP_DIGITS = 6
TOTP_SECRET_BYTES = 20
RECOVERY_CODE_COUNT = 10
RECOVERY_CODE_BYTES = 16


def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(TOTP_SECRET_BYTES)).decode(
        "ascii"
    ).rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    padding = "=" * ((8 - len(normalized) % 8) % 8)
    try:
        value = base64.b32decode(normalized + padding, casefold=True)
    except Exception as exc:
        raise ValueError("invalid TOTP secret") from exc
    if len(value) < TOTP_SECRET_BYTES:
        raise ValueError("TOTP secret is too short")
    return value


def totp_code(secret: str, *, step: int | None = None) -> str:
    counter = int(time.time()) // TOTP_PERIOD_SECONDS if step is None else step
    if counter < 0:
        raise ValueError("TOTP counter must be non-negative")
    digest = hmac.new(
        _decode_secret(secret),
        struct.pack(">Q", counter),
        hashlib.sha1,
    ).digest()
    offset = digest[-1] & 0x0F
    dynamic = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return f"{dynamic % (10 ** TOTP_DIGITS):0{TOTP_DIGITS}d}"


def matching_totp_step(
    secret: str,
    code: str,
    *,
    now: float | None = None,
    allowed_skew_steps: int = 1,
) -> int | None:
    normalized = code.strip().replace(" ", "")
    if len(normalized) != TOTP_DIGITS or not normalized.isdigit():
        return None
    current = int(time.time() if now is None else now) // TOTP_PERIOD_SECONDS
    # Prefer the current window, then tolerate one adjacent device-clock step.
    offsets = [0]
    for distance in range(1, allowed_skew_steps + 1):
        offsets.extend((-distance, distance))
    for offset in offsets:
        step = current + offset
        if step >= 0 and hmac.compare_digest(totp_code(secret, step=step), normalized):
            return step
    return None


def provisioning_uri(*, secret: str, account_name: str, issuer: str) -> str:
    label = quote(f"{issuer}:{account_name}", safe="")
    return (
        f"otpauth://totp/{label}?secret={quote(secret, safe='')}"
        f"&issuer={quote(issuer, safe='')}&algorithm=SHA1"
        f"&digits={TOTP_DIGITS}&period={TOTP_PERIOD_SECONDS}"
    )


def new_recovery_codes() -> list[str]:
    values: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = base64.b32encode(secrets.token_bytes(RECOVERY_CODE_BYTES)).decode(
            "ascii"
        ).rstrip("=")
        values.append("-".join(raw[index : index + 5] for index in range(0, 25, 5)))
    return values


def normalize_recovery_code(code: str) -> str:
    return "".join(character for character in code.upper() if character.isalnum())


def recovery_code_hash(*, user_id: str, code: str) -> str:
    return keyed_lookup_digest(
        domain="vibecanvas:mfa-recovery-code:v1",
        components=(user_id,),
        value=normalize_recovery_code(code),
    )
