"""Canonical, reversible identities for Chat workspaces.

Workspace identifiers are routing keys, never authorization evidence.  The
previous shape embedded a user-id prefix and encouraged callers to treat that
prefix as an ownership check.  The current shape carries only an encoded Chat
id; every HTTP/Runtime entry point resolves it to the Chat authorization root
and asks OpenFGA.
"""

from __future__ import annotations

import base64


_PREFIX = "__chatws_v2_"


def chat_workspace_scope_id(chat_id: str) -> str:
    """Return the deterministic VFS/sandbox scope for one Chat.

    URL-safe base64 is reversible and collision-free for UTF-8 Chat ids.  It is
    distinct from ``chats.scope_id``, which groups history entries under a UI
    carrier such as the general Chat page.
    """
    raw = str(chat_id).encode("utf-8")
    if not raw:
        raise ValueError("chat_id must not be empty")
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    return f"{_PREFIX}{encoded}"


def chat_id_from_workspace_scope(scope_id: str) -> str | None:
    """Decode a canonical scope, returning ``None`` for any invalid input."""
    if not scope_id or not scope_id.startswith(_PREFIX):
        return None
    encoded = scope_id[len(_PREFIX):]
    if not encoded:
        return None
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        value = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not value or chat_workspace_scope_id(value) != scope_id:
        return None
    return value


__all__ = ["chat_id_from_workspace_scope", "chat_workspace_scope_id"]
