"""Short-lived, audience- and browser-bound capability for the MV3 extension."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time


BROWSER_TOKEN_AUDIENCE = "vibecanvas.browser-ws.v1"
MAX_BROWSER_TOKEN_TTL_S = 900


@dataclass(frozen=True)
class ScopedAuth:
    user_id: str
    tenant_id: str
    wf_id: str
    browser_id: str
    extension_id: str
    session_id: str
    session_generation: int
    session_audience: str
    iat: int
    exp: int
    audience: str = BROWSER_TOKEN_AUDIENCE


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64u_dec(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(body: str, secret: str) -> str:
    return _b64u(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def mint_scoped_token(
    user_id: str,
    tenant_id: str,
    wf_id: str,
    secret: str,
    *,
    browser_id: str,
    extension_id: str,
    session_id: str,
    session_generation: int,
    session_audience: str,
    ttl_s: int = MAX_BROWSER_TOKEN_TTL_S,
    now: int | None = None,
) -> str:
    """Mint one narrowly scoped token; callers cannot extend it past 15 minutes."""
    if ttl_s <= 0 or ttl_s > MAX_BROWSER_TOKEN_TTL_S:
        raise ValueError("browser token TTL is outside the supported boundary")
    if session_generation <= 0:
        raise ValueError("session generation must be positive")
    issued_at = int(time.time()) if now is None else now
    payload = {
        "aud": BROWSER_TOKEN_AUDIENCE,
        "b": browser_id,
        "e": extension_id,
        "exp": issued_at + ttl_s,
        "g": session_generation,
        "iat": issued_at,
        "sa": session_audience,
        "sid": session_id,
        "t": tenant_id,
        "u": user_id,
        "w": wf_id,
    }
    body = _b64u(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_sign(body, secret)}"


def verify_scoped_token(
    token: str,
    secret: str,
    now: int | None = None,
) -> ScopedAuth | None:
    """Verify signature and strict claims without accepting a legacy token shape."""
    current = int(time.time()) if now is None else now
    try:
        body, signature = token.rsplit(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(signature, _sign(body, secret)):
        return None
    try:
        payload = json.loads(_b64u_dec(body))
    except Exception:
        return None
    required = {"aud", "b", "e", "exp", "g", "iat", "sa", "sid", "t", "u", "w"}
    if not isinstance(payload, dict) or set(payload) != required:
        return None
    if payload.get("aud") != BROWSER_TOKEN_AUDIENCE:
        return None
    string_claims = ("b", "e", "sa", "sid", "t", "u", "w")
    if any(
        not isinstance(payload.get(key), str)
        or not payload[key]
        or len(payload[key]) > 512
        for key in string_claims
    ):
        return None
    if type(payload.get("g")) is not int or payload["g"] <= 0:
        return None
    if type(payload.get("iat")) is not int or type(payload.get("exp")) is not int:
        return None
    issued_at = payload["iat"]
    expires_at = payload["exp"]
    if issued_at > current + 30 or expires_at <= current:
        return None
    if expires_at <= issued_at or expires_at - issued_at > MAX_BROWSER_TOKEN_TTL_S:
        return None
    return ScopedAuth(
        user_id=payload["u"],
        tenant_id=payload["t"],
        wf_id=payload["w"],
        browser_id=payload["b"],
        extension_id=payload["e"],
        session_id=payload["sid"],
        session_generation=payload["g"],
        session_audience=payload["sa"],
        iat=issued_at,
        exp=expires_at,
    )


__all__ = [
    "BROWSER_TOKEN_AUDIENCE",
    "MAX_BROWSER_TOKEN_TTL_S",
    "ScopedAuth",
    "mint_scoped_token",
    "verify_scoped_token",
]
