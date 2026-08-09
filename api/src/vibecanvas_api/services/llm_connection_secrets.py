"""Separate LLM proxy/API URL credentials from DB-visible URL metadata."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.secret_service import secret_service


def _redact_url(value: str | None) -> tuple[str | None, str | None]:
    """Split a connection URL into safe routing metadata and a secret value.

    The database projection contains only scheme/host/port/path/fragment.  It
    deliberately does not retain redacted user-info or query names: strings
    such as ``***:***@`` are still URL credentials structurally, violate the
    strict schema, and reveal that a credential is embedded in a particular
    field.  The complete original is kept only in SecretService.
    """
    if not value:
        return value, None
    try:
        parts = urlsplit(value)
        sensitive = bool(parts.username or parts.password or parts.query)
        if not sensitive:
            return value, None
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port:
            host = f"{host}:{parts.port}"
        return (
            urlunsplit((parts.scheme, host, parts.path, "", parts.fragment)),
            value,
        )
    except ValueError:
        # Invalid URLs are validated by their downstream client. Do not risk
        # copying a potentially credential-bearing malformed value elsewhere.
        return None, value


def split_llm_connection_credentials(
    *, api_url: str | None, proxy: str | None,
) -> tuple[str | None, str | None, dict[str, Any] | None]:
    stored_api_url, secret_api_url = _redact_url(api_url)
    stored_proxy, secret_proxy = _redact_url(proxy)
    payload: dict[str, Any] = {}
    if secret_api_url is not None:
        payload["api_url"] = secret_api_url
    if secret_proxy is not None:
        payload["proxy"] = secret_proxy
    return stored_api_url, stored_proxy, payload or None


async def store_llm_connection_credentials(
    session: AsyncSession,
    *,
    tenant_id,
    credential_id,
    api_url: str | None,
    proxy: str | None,
    version: int,
):
    stored_api_url, stored_proxy, payload = split_llm_connection_credentials(
        api_url=api_url,
        proxy=proxy,
    )
    if payload is None:
        return stored_api_url, stored_proxy, None
    ref = await secret_service().put_text(
        session,
        tenant_id=tenant_id,
        purpose="llm_connection_credentials",
        resource_type="llm_credential",
        resource_id=credential_id,
        plaintext=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        version=version,
    )
    return stored_api_url, stored_proxy, ref


async def hydrate_llm_connection_credentials(
    session: AsyncSession,
    credential: dict,
) -> dict:
    if not credential.get("connection_secret_ref"):
        return dict(credential)
    plaintext = await secret_service().resolve_text(
        session,
        secret_ref=credential["connection_secret_ref"],
        tenant_id=credential["tenant_id"],
        purpose="llm_connection_credentials",
        resource_type="llm_credential",
        resource_id=credential["id"],
    )
    try:
        payload = json.loads(plaintext)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("stored LLM connection secret is invalid") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("stored LLM connection secret is invalid")
    hydrated = dict(credential)
    for key in ("api_url", "proxy"):
        if isinstance(payload.get(key), str):
            hydrated[key] = payload[key]
    return hydrated
