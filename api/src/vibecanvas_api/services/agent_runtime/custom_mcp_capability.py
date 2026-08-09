"""Turn-scoped capabilities for host-brokered custom remote MCP access."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time


_DOMAIN = b"vibecanvas:runtime-custom-mcp:v1\0"
_AUDIENCE = "runtime-custom-mcp"
_MAX_TOKEN_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class RuntimeCustomMcpCapability:
    organization_id: str
    user_id: str
    chat_id: str
    turn_id: str
    runtime_session_id: str
    session_id: str
    session_generation: int
    membership_id: str
    server_id: str
    transport: str
    config_revision: str
    authorization_generation: str
    issued_at: int
    expires_at: int
    audience: str = _AUDIENCE


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _signature(body: str, secret: str) -> str:
    return _b64url(
        hmac.new(
            secret.encode(), _DOMAIN + body.encode("ascii"), hashlib.sha256
        ).digest()
    )


def mcp_config_revision(*, server_id: object, updated_at: object) -> str:
    return hashlib.sha256(
        f"{server_id}\0{updated_at}".encode("utf-8")
    ).hexdigest()


def mint_runtime_custom_mcp_capability(
    *,
    organization_id: str,
    user_id: str,
    chat_id: str,
    turn_id: str,
    runtime_session_id: str,
    session_id: str,
    session_generation: int,
    membership_id: str,
    server_id: str,
    transport: str,
    config_revision: str,
    authorization_generation: str,
    secret: str,
    ttl_s: int,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    payload = {
        "v": 1,
        "aud": _AUDIENCE,
        "o": organization_id,
        "u": user_id,
        "c": chat_id,
        "t": turn_id,
        "rs": runtime_session_id,
        "sid": session_id,
        "sg": int(session_generation),
        "mid": membership_id,
        "server": server_id,
        "transport": transport,
        "cr": config_revision,
        "ag": authorization_generation,
        "res": [f"chat:{chat_id}", f"mcp_installation:{server_id}"],
        "act": ["chat:execute", "mcp_installation:use", "mcp:call"],
        "iat": issued_at,
        "exp": issued_at + max(1, int(ttl_s)),
    }
    body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    token = f"{body}.{_signature(body, secret)}"
    if len(token) > _MAX_TOKEN_BYTES:  # pragma: no cover
        raise ValueError("runtime custom MCP capability is too large")
    return token


def verify_runtime_custom_mcp_capability(
    token: str,
    *,
    secret: str,
    server_id: str,
    now: int | None = None,
) -> RuntimeCustomMcpCapability | None:
    current = int(time.time()) if now is None else int(now)
    if not token or len(token) > _MAX_TOKEN_BYTES:
        return None
    try:
        body, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _signature(body, secret)):
            return None
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw)
        if payload.get("v") != 1 or payload.get("aud") != _AUDIENCE:
            return None
        if str(payload["server"]) != server_id:
            return None
        resources = {str(item) for item in payload["res"]}
        actions = {str(item) for item in payload["act"]}
        if resources != {
            f"chat:{payload['c']}",
            f"mcp_installation:{server_id}",
        } or actions != {
            "chat:execute",
            "mcp_installation:use",
            "mcp:call",
        }:
            return None
        capability = RuntimeCustomMcpCapability(
            organization_id=str(payload["o"]),
            user_id=str(payload["u"]),
            chat_id=str(payload["c"]),
            turn_id=str(payload["t"]),
            runtime_session_id=str(payload["rs"]),
            session_id=str(payload["sid"]),
            session_generation=int(payload["sg"]),
            membership_id=str(payload["mid"]),
            server_id=server_id,
            transport=str(payload["transport"]),
            config_revision=str(payload["cr"]),
            authorization_generation=str(payload["ag"]),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if capability.issued_at > current + 30 or capability.expires_at <= current:
        return None
    return capability


__all__ = [
    "RuntimeCustomMcpCapability",
    "mcp_config_revision",
    "mint_runtime_custom_mcp_capability",
    "verify_runtime_custom_mcp_capability",
]
