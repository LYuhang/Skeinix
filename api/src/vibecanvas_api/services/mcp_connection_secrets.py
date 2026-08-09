"""Store MCP connection credentials separately from structural configuration."""
from __future__ import annotations

import copy
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.secret_service import secret_service


def _split_url(value: str) -> tuple[str, str | None]:
    """Return a query-free public URL plus the original when it had values.

    The strict database projection intentionally does not retain parameter
    names or redacted placeholders.  Query structure can itself reveal a
    provider, account shape, or credential scheme; the complete original URL
    therefore belongs in SecretService and is hydrated only in host memory.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return value, None
    if not parts.query:
        return value, None
    return (
        urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment)),
        value,
    )


def split_connection_credentials(
    *, endpoint: str, connection_config: dict | None,
) -> tuple[str, dict[str, Any], dict[str, Any] | None]:
    """Separate credential values from the DB-visible structure.

    Header and environment values are always credentials. URL query values are
    credentials regardless of parameter name, preventing custom provider keys
    from escaping a brittle name-based classifier.
    """
    stored = copy.deepcopy(connection_config or {})
    payload: dict[str, Any] = {}
    stored_endpoint, secret_endpoint = _split_url(endpoint)
    if secret_endpoint is not None:
        payload["endpoint"] = secret_endpoint

    for key in ("headers", "env"):
        values = stored.get(key)
        if isinstance(values, dict) and values:
            payload[key] = copy.deepcopy(values)
            # Header/environment names are part of the private connection
            # material too. Keep the durable public projection empty and
            # restore the whole mapping only inside the host broker.
            stored[key] = {}

    url = stored.get("url")
    if isinstance(url, str):
        stored_url, secret_url = _split_url(url)
        stored["url"] = stored_url
        if secret_url is not None:
            payload["url"] = secret_url

    return stored_endpoint, stored, payload or None


async def store_connection_credentials(
    session: AsyncSession,
    *,
    tenant_id,
    server_id,
    endpoint: str,
    connection_config: dict | None,
    version: int,
) -> tuple[str, dict[str, Any], object | None]:
    stored_endpoint, stored_config, payload = split_connection_credentials(
        endpoint=endpoint,
        connection_config=connection_config,
    )
    if payload is None:
        return stored_endpoint, stored_config, None
    secret_ref = await secret_service().put_text(
        session,
        tenant_id=tenant_id,
        purpose="mcp_connection_credentials",
        resource_type="mcp_installation",
        resource_id=server_id,
        plaintext=json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        version=version,
    )
    return stored_endpoint, stored_config, secret_ref


async def hydrate_connection_credentials(
    session: AsyncSession,
    server: dict,
) -> dict:
    """Return a request-local row containing the full connection material."""
    if not server.get("connection_secret_ref"):
        return dict(server)
    plaintext = await secret_service().resolve_text(
        session,
        secret_ref=server["connection_secret_ref"],
        tenant_id=server["tenant_id"],
        purpose="mcp_connection_credentials",
        resource_type="mcp_installation",
        resource_id=server["id"],
    )
    try:
        payload = json.loads(plaintext)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("stored MCP connection secret is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stored MCP connection secret is invalid")

    hydrated = dict(server)
    config = copy.deepcopy(server.get("connection_config") or {})
    for key in ("headers", "env"):
        if isinstance(payload.get(key), dict):
            config[key] = payload[key]
    if isinstance(payload.get("url"), str):
        config["url"] = payload["url"]
    hydrated["connection_config"] = config
    if isinstance(payload.get("endpoint"), str):
        hydrated["endpoint"] = payload["endpoint"]
    return hydrated
