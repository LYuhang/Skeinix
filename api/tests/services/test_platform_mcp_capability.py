from __future__ import annotations

import base64
import hashlib
import hmac
import json

from vibecanvas_api.services.platform_mcp.capability import (
    mint_platform_mcp_capability,
    platform_mcp_policy,
    verify_platform_mcp_capability,
)


_KWARGS = {
    "organization_id": "11111111-1111-1111-1111-111111111111",
    "user_id": "22222222-2222-2222-2222-222222222222",
    "chat_id": "chat-1",
    "turn_id": "turn-1",
    "workspace_scope_id": "workspace-1",
    "runtime_session_id": "runtime-session-1",
    "session_id": "33333333-3333-3333-3333-333333333333",
    "session_generation": 7,
    "membership_id": "44444444-4444-4444-4444-444444444444",
    "authorization_generation": "authz-generation-1",
    "secret": "secret",
    "ttl_s": 60,
    "now": 100,
}


def _resign(payload: dict, secret: str = "secret") -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).rstrip(b"=").decode()
    digest = hmac.new(
        secret.encode(),
        b"vibecanvas:platform-mcp:v1\0" + body.encode("ascii"),
        hashlib.sha256,
    ).digest()
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return f"{body}.{signature}"


def _payload(token: str) -> dict:
    body = token.split(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))


def test_platform_mcp_capability_binds_full_execution_and_policy_scope() -> None:
    token = mint_platform_mcp_capability(server="workflow", **_KWARGS)

    capability = verify_platform_mcp_capability(
        token, secret="secret", server="workflow", now=120
    )
    assert capability is not None
    assert capability.audience == "platform-mcp"
    assert capability.organization_id == _KWARGS["organization_id"]
    assert capability.chat_id == "chat-1"
    assert capability.turn_id == "turn-1"
    assert capability.workspace_scope_id == "workspace-1"
    assert capability.runtime_session_id == "runtime-session-1"
    assert capability.session_id == _KWARGS["session_id"]
    assert capability.session_generation == 7
    assert capability.membership_id == _KWARGS["membership_id"]
    assert capability.authorization_generation == "authz-generation-1"
    assert capability.approval_mode == "agent"
    expected = platform_mcp_policy(
        organization_id=str(_KWARGS["organization_id"]),
        chat_id="chat-1",
        workspace_scope_id="workspace-1",
        server="workflow",
    )
    assert capability.resources == expected.resources
    assert capability.actions == expected.actions
    assert verify_platform_mcp_capability(
        token, secret="secret", server="browser", now=120
    ) is None
    assert verify_platform_mcp_capability(
        token, secret="wrong", server="workflow", now=120
    ) is None
    assert verify_platform_mcp_capability(
        token, secret="secret", server="workflow", now=160
    ) is None


def test_platform_mcp_capability_rejects_resigned_scope_widening() -> None:
    token = mint_platform_mcp_capability(server="workflow", **_KWARGS)
    payload = _payload(token)
    payload["act"].append("workflow:delete")
    widened = _resign(payload)

    assert verify_platform_mcp_capability(
        widened,
        secret="secret",
        server="workflow",
        now=120,
    ) is None


def test_platform_mcp_capability_rejects_wrong_audience_or_future_issue() -> None:
    token = mint_platform_mcp_capability(server="workflow", **_KWARGS)
    payload = _payload(token)
    payload["aud"] = "runtime-model"
    assert verify_platform_mcp_capability(
        _resign(payload), secret="secret", now=120
    ) is None

    future = mint_platform_mcp_capability(
        server="workflow",
        **{**_KWARGS, "now": 151},
    )
    assert verify_platform_mcp_capability(
        future, secret="secret", now=120
    ) is None


def test_platform_mcp_capability_rejects_incomplete_identity() -> None:
    try:
        mint_platform_mcp_capability(
            server="workflow",
            **{**_KWARGS, "session_id": ""},
        )
    except ValueError as exc:
        assert "identity is incomplete" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("empty Session identity was accepted")


def test_platform_mcp_capability_binds_trusted_approval_mode() -> None:
    token = mint_platform_mcp_capability(
        server="build",
        approval_mode="always_ask",
        **_KWARGS,
    )
    capability = verify_platform_mcp_capability(
        token, secret="secret", server="build", now=120,
    )
    assert capability is not None
    assert capability.approval_mode == "always_ask"

    payload = _payload(token)
    payload["am"] = "untrusted"
    assert verify_platform_mcp_capability(
        _resign(payload), secret="secret", server="build", now=120,
    ) is None
