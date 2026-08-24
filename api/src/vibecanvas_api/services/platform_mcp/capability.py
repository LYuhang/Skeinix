"""Short-lived least-privilege capabilities for built-in Platform MCP.

The scope carried by this token is a ceiling, not an authorization decision.
Concrete resource ids still come from tool arguments and are checked against
the live ``AuthzService`` by the trusted host before a tool implementation is
called.  Keeping the ceiling in the signed token prevents a Runtime from using
a descriptor for a different server or a broader class of operations.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
import json
import time


_DOMAIN = b"vibecanvas:platform-mcp:v1\0"
_AUDIENCE = "platform-mcp"
_MAX_TOKEN_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class PlatformMcpPolicy:
    resources: tuple[str, ...]
    actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PlatformMcpCapability:
    organization_id: str
    user_id: str
    chat_id: str
    turn_id: str
    workspace_scope_id: str
    runtime_session_id: str
    session_id: str
    session_generation: int
    membership_id: str
    server: str
    authorization_generation: str
    approval_mode: str
    resources: tuple[str, ...]
    actions: tuple[str, ...]
    issued_at: int
    expires_at: int
    audience: str = _AUDIENCE

    @property
    def tenant_id(self) -> str:
        """Compatibility alias while physical tenant columns mean organization."""
        return self.organization_id

    @property
    def exp(self) -> int:
        """Compatibility alias used by the MCP SDK's AccessToken projection."""
        return self.expires_at


_SERVER_POLICY: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "config": (
        frozenset({"platform_catalog:*", "llm_credential:*"}),
        frozenset({"platform_catalog:view", "llm_credential:view_metadata"}),
    ),
    "interactive": (
        frozenset({"interactive_artifact:*", "vfs_path:*"}),
        frozenset({
            "interactive_artifact:create",
            "vfs_path:view",
            "vfs_path:update",
        }),
    ),
    "workflow": (
        frozenset({"workflow:*"}),
        frozenset({"workflow:view_metadata", "workflow:view"}),
    ),
    "build": (
        frozenset({"workflow:*", "vfs_path:*"}),
        frozenset(
            {
                "organization:create",
                "workflow:view",
                "workflow:use",
                "workflow:update",
                "workflow:execute",
                "vfs_path:view",
                "vfs_path:update",
            }
        ),
    ),
    "task": (
        frozenset({"task:*", "workflow:*"}),
        frozenset(
            {
                "organization:create",
                "task:view_metadata",
                "task:view",
                "task:inspect_runs",
                "task:create",
                "task:update",
                "task:delete",
                "task:cancel",
                "task:resume",
                "workflow:use",
            }
        ),
    ),
    "deployment": (
        frozenset({"deployment:*", "workflow:*"}),
        frozenset(
            {
                "organization:create",
                "deployment:view_metadata",
                "deployment:view",
                "deployment:inspect_runs",
                "deployment:deploy",
                "deployment:update",
                "deployment:delete",
                "workflow:deploy",
            }
        ),
    ),
    "knowledge": (
        frozenset({"knowledge_base:*"}),
        frozenset(
            {
                "organization:create",
                "knowledge_base:view_metadata",
                "knowledge_base:view",
                "knowledge_base:use",
                "knowledge_base:create",
                "knowledge_base:update",
                "knowledge_base:delete",
            }
        ),
    ),
    "browser": (
        frozenset({"browser_binding:*", "vfs_path:*"}),
        frozenset(
            {
                "browser_binding:view",
                "browser_binding:use",
                "browser_binding:update",
                "vfs_path:update",
            }
        ),
    ),
}


def platform_mcp_policy(
    *,
    organization_id: str,
    chat_id: str,
    workspace_scope_id: str,
    server: str,
) -> PlatformMcpPolicy:
    """Return the exact host-owned ceiling for one Platform MCP descriptor."""
    try:
        resource_patterns, action_patterns = _SERVER_POLICY[server]
    except KeyError as exc:
        raise ValueError(f"unknown Platform MCP server: {server}") from exc
    required = {
        "organization_id": organization_id,
        "chat_id": chat_id,
        "workspace_scope_id": workspace_scope_id,
    }
    if any(not str(value).strip() for value in required.values()):
        raise ValueError("Platform MCP policy identity is incomplete")
    resources = {
        f"organization:{organization_id}",
        f"chat:{chat_id}",
        f"chat_workspace:{workspace_scope_id}",
        f"platform_mcp:{server}",
        *resource_patterns,
    }
    actions = {"chat:execute", "platform_mcp:call", *action_patterns}
    return PlatformMcpPolicy(
        resources=tuple(sorted(resources)),
        actions=tuple(sorted(actions)),
    )


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _signature(body: str, secret: str) -> str:
    digest = hmac.new(
        secret.encode("utf-8"), _DOMAIN + body.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64url(digest)


def mint_platform_mcp_capability(
    *,
    organization_id: str,
    user_id: str,
    chat_id: str,
    turn_id: str,
    workspace_scope_id: str,
    runtime_session_id: str,
    session_id: str,
    session_generation: int,
    membership_id: str,
    server: str,
    authorization_generation: str,
    secret: str,
    ttl_s: int,
    approval_mode: str = "agent",
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else int(now)
    if approval_mode not in {"agent", "always_ask", "always_allow"}:
        raise ValueError("invalid Platform MCP approval mode")
    if any(
        not str(value).strip()
        for value in (
            organization_id,
            user_id,
            chat_id,
            turn_id,
            workspace_scope_id,
            runtime_session_id,
            session_id,
            membership_id,
            authorization_generation,
        )
    ) or int(session_generation) <= 0:
        raise ValueError("Platform MCP capability identity is incomplete")
    policy = platform_mcp_policy(
        organization_id=organization_id,
        chat_id=chat_id,
        workspace_scope_id=workspace_scope_id,
        server=server,
    )
    payload = {
        "v": 1,
        "aud": _AUDIENCE,
        "o": organization_id,
        "u": user_id,
        "c": chat_id,
        "t": turn_id,
        "w": workspace_scope_id,
        "rs": runtime_session_id,
        "sid": session_id,
        "sg": int(session_generation),
        "mid": membership_id,
        "s": server,
        "ag": authorization_generation,
        "am": approval_mode,
        "res": list(policy.resources),
        "act": list(policy.actions),
        "iat": issued_at,
        "exp": issued_at + max(1, int(ttl_s)),
    }
    body = _b64url(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    token = f"{body}.{_signature(body, secret)}"
    if len(token.encode("ascii")) > _MAX_TOKEN_BYTES:  # pragma: no cover
        raise ValueError("Platform MCP capability is too large")
    return token


def verify_platform_mcp_capability(
    token: str,
    *,
    secret: str,
    server: str | None = None,
    now: int | None = None,
) -> PlatformMcpCapability | None:
    current = int(time.time()) if now is None else int(now)
    if not token or len(token.encode("utf-8", errors="ignore")) > _MAX_TOKEN_BYTES:
        return None
    try:
        body, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(signature, _signature(body, secret)):
            return None
        payload = json.loads(_decode(body))
        if payload.get("v") != 1 or payload.get("aud") != _AUDIENCE:
            return None
        capability = PlatformMcpCapability(
            organization_id=str(payload["o"]),
            user_id=str(payload["u"]),
            chat_id=str(payload["c"]),
            turn_id=str(payload["t"]),
            workspace_scope_id=str(payload["w"]),
            runtime_session_id=str(payload["rs"]),
            session_id=str(payload["sid"]),
            session_generation=int(payload["sg"]),
            membership_id=str(payload["mid"]),
            server=str(payload["s"]),
            authorization_generation=str(payload["ag"]),
            approval_mode=str(payload.get("am") or "agent"),
            resources=tuple(str(item) for item in payload["res"]),
            actions=tuple(str(item) for item in payload["act"]),
            issued_at=int(payload["iat"]),
            expires_at=int(payload["exp"]),
        )
        expected_policy = platform_mcp_policy(
            organization_id=capability.organization_id,
            chat_id=capability.chat_id,
            workspace_scope_id=capability.workspace_scope_id,
            server=capability.server,
        )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        json.JSONDecodeError,
        binascii.Error,
    ):
        return None
    if server is not None and capability.server != server:
        return None
    if capability.issued_at > current + 30 or capability.expires_at <= current:
        return None
    if capability.expires_at <= capability.issued_at:
        return None
    if capability.session_generation <= 0:
        return None
    if capability.approval_mode not in {"agent", "always_ask", "always_allow"}:
        return None
    if capability.resources != expected_policy.resources:
        return None
    if capability.actions != expected_policy.actions:
        return None
    return capability


__all__ = [
    "PlatformMcpCapability",
    "PlatformMcpPolicy",
    "mint_platform_mcp_capability",
    "platform_mcp_policy",
    "verify_platform_mcp_capability",
]
