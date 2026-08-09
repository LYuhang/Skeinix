"""Resolve MCP bearer auth only inside the host execution boundary."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.secret_service import secret_service


async def resolve_mcp_bearer_auth_config(
    session: AsyncSession,
    server: dict,
) -> dict:
    auth = dict(server.get("auth_config") or {"type": "none"})
    if auth.get("type") != "bearer":
        return {"type": "none"}
    secret_ref = server.get("auth_secret_ref")
    if not secret_ref:
        raise RuntimeError("MCP bearer configuration has no SecretService reference")
    token = await secret_service().resolve_text(
        session,
        secret_ref=secret_ref,
        tenant_id=server["tenant_id"],
        purpose="mcp_bearer_token",
        resource_type="mcp_installation",
        resource_id=server["id"],
    )
    return {"type": "bearer", "token": token}
