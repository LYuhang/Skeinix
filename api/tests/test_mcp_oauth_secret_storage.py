from __future__ import annotations

from datetime import datetime, timezone
import uuid
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sqlalchemy import text

from vibecanvas_api.config import config
from vibecanvas_api.services.mcp_oauth import (
    begin_connection,
    complete_connection,
    disconnect,
    resolve_oauth_auth_config,
)
from vibecanvas_api.storage.db import session_scope


async def _seed_server(engine) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    server_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:id,'oauth-secret')"),
            {"id": tenant_id},
        )
        await connection.execute(
            text(
                "INSERT INTO users(user_id,tenant_id,email) "
                "VALUES (:user_id,:tenant_id,:email)"
            ),
            {
                "user_id": user_id,
                "tenant_id": tenant_id,
                "email": f"{user_id}@oauth.test",
            },
        )
        await connection.execute(
            text(
                "INSERT INTO mcp_servers("
                "id,tenant_id,user_id,name,tool_prefix,transport,endpoint,"
                "auth_mode,auth_metadata_url,connection_status,auth_config"
                ") VALUES ("
                ":id,:tenant_id,:user_id,'OAuth MCP','oauthmcp','sse',"
                "'https://mcp.example.com/sse','oauth',"
                "'https://mcp.example.com/.well-known/oauth-protected-resource',"
                "'connection_required','{}'::jsonb)"
            ),
            {"id": server_id, "tenant_id": tenant_id, "user_id": user_id},
        )
    return tenant_id, user_id, server_id


@pytest.mark.asyncio
async def test_oauth_pkce_and_tokens_only_persist_as_secret_refs(
    pg_engine, monkeypatch,
) -> None:
    tenant_id, user_id, server_id = await _seed_server(pg_engine)
    monkeypatch.setattr(
        config.public_urls,
        "public_url",
        "https://app.example.com",
    )
    discovered = {
        "resource": "https://mcp.example.com/sse",
        "issuer": "https://auth.example.com",
        "authorization_endpoint": "https://auth.example.com/authorize",
        "token_endpoint": "https://auth.example.com/token",
        "registration_endpoint": None,
        "client_id_metadata_document_supported": True,
        "revocation_endpoint": None,
        "scopes": ["mcp:use"],
    }
    server = {
        "id": server_id,
        "endpoint": "https://mcp.example.com/sse",
        "auth_metadata_url": (
            "https://mcp.example.com/.well-known/oauth-protected-resource"
        ),
    }
    with patch(
        "vibecanvas_api.services.mcp_oauth.discover_authorization",
        new=AsyncMock(return_value=discovered),
    ):
        async with session_scope(tenant_id=str(tenant_id)) as session:
            authorization_url = await begin_connection(
                session,
                server=server,
                tenant_id=tenant_id,
                user_id=user_id,
                return_origin="https://app.example.com/settings",
            )
            await session.commit()

    state = authorization_url.split("state=", 1)[1].split("&", 1)[0]
    async with pg_engine.connect() as connection:
        transaction = (
            await connection.execute(
                text("SELECT secret_ref FROM mcp_oauth_transactions")
            )
        ).mappings().one()
        assert transaction["secret_ref"] is not None
        columns = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND "
                        "table_name='mcp_oauth_transactions'"
                    )
                )
            ).all()
        }
        assert {"code_verifier_encrypted", "client_secret_encrypted"}.isdisjoint(
            columns
        )

    token_response = Mock()
    token_response.raise_for_status.return_value = None
    token_response.json.return_value = {
        "access_token": "access-token-never-in-business-table",
        "refresh_token": "refresh-token-never-in-business-table",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    with patch(
        "vibecanvas_api.services.mcp_oauth.request_pinned_public_url",
        new=AsyncMock(return_value=token_response),
    ):
        async with session_scope(tenant_id=str(tenant_id)) as session:
            _, connected_server = await complete_connection(
                session,
                state=state,
                code="one-time-provider-code",
            )
            auth = await resolve_oauth_auth_config(session, connected_server)
            assert auth == {
                "type": "bearer",
                "token": "access-token-never-in-business-table",
            }
            await session.commit()

    async with pg_engine.connect() as connection:
        connection_row = (
            await connection.execute(
                text(
                    "SELECT secret_ref, expires_at FROM mcp_oauth_connections "
                    "WHERE server_id = :server_id"
                ),
                {"server_id": server_id},
            )
        ).mappings().one()
        assert connection_row["secret_ref"] is not None
        columns = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND "
                        "table_name='mcp_oauth_connections'"
                    )
                )
            ).all()
        }
        assert {
            "client_secret_encrypted",
            "access_token_encrypted",
            "refresh_token_encrypted",
        }.isdisjoint(columns)
        assert connection_row["expires_at"] > datetime.now(timezone.utc)
        assert (
            await connection.execute(
                text("SELECT count(*) FROM mcp_oauth_transactions")
            )
        ).scalar_one() == 0
        ciphertext = (
            await connection.execute(
                text(
                    "SELECT ciphertext FROM encrypted_secrets "
                    "WHERE secret_id = :secret_ref"
                ),
                {"secret_ref": connection_row["secret_ref"]},
            )
        ).scalar_one()
        assert "access-token" not in ciphertext
        assert "refresh-token" not in ciphertext

    async with session_scope(tenant_id=str(tenant_id)) as session:
        await disconnect(session, connected_server)
        await session.commit()
    async with pg_engine.connect() as connection:
        assert (
            await connection.execute(
                text(
                    "SELECT count(*) FROM mcp_oauth_connections "
                    "WHERE server_id = :server_id"
                ),
                {"server_id": server_id},
            )
        ).scalar_one() == 0
        assert (
            await connection.execute(
                text(
                    "SELECT status FROM encrypted_secrets "
                    "WHERE secret_id = :secret_ref"
                ),
                {"secret_ref": connection_row["secret_ref"]},
            )
        ).scalar_one() == "destroyed"
