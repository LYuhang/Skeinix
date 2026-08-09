from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.services.mcp_connection_secrets import (
    hydrate_connection_credentials,
    split_connection_credentials,
    store_connection_credentials,
)
from vibecanvas_api.storage.db import session_scope


def test_split_connection_credentials_removes_private_projection() -> None:
    endpoint, stored, payload = split_connection_credentials(
        endpoint="https://mcp.example.com/sse?workspace=acme&key=secret-1",
        connection_config={
            "url": "https://mcp.example.com/sse?region=us&token=secret-2",
            "headers": {"X-Api-Key": "secret-3", "X-Empty": ""},
            "env": {"SERVICE_TOKEN": "secret-4"},
            "timeout": 30,
        },
    )
    assert endpoint == "https://mcp.example.com/sse"
    assert stored["url"] == "https://mcp.example.com/sse"
    assert stored["headers"] == {}
    assert stored["env"] == {}
    assert stored["timeout"] == 30
    assert payload == {
        "endpoint": (
            "https://mcp.example.com/sse?workspace=acme&key=secret-1"
        ),
        "headers": {"X-Api-Key": "secret-3", "X-Empty": ""},
        "env": {"SERVICE_TOKEN": "secret-4"},
        "url": (
            "https://mcp.example.com/sse?region=us&token=secret-2"
        ),
    }


@pytest.mark.asyncio
async def test_connection_credentials_round_trip_only_through_secret_ref(
    pg_engine,
) -> None:
    tenant_id = uuid.uuid4()
    server_id = uuid.uuid4()
    async with pg_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:id,'mcp-conn')"),
            {"id": tenant_id},
        )

    full_endpoint = "https://mcp.example.com/sse?api_key=never-plaintext"
    full_config = {
        "url": full_endpoint,
        "headers": {"Authorization": "Custom never-plaintext-header"},
        "env": {"PRIVATE_TOKEN": "never-plaintext-env"},
        "timeout": 12,
    }
    async with session_scope(tenant_id=str(tenant_id)) as session:
        endpoint, stored, secret_ref = await store_connection_credentials(
            session,
            tenant_id=tenant_id,
            server_id=server_id,
            endpoint=full_endpoint,
            connection_config=full_config,
            version=1,
        )
        assert secret_ref is not None
        assert "never-plaintext" not in str(stored)
        assert "?" not in endpoint
        assert "?" not in stored["url"]
        assert stored["headers"] == {}
        assert stored["env"] == {}
        hydrated = await hydrate_connection_credentials(
            session,
            {
                "id": server_id,
                "tenant_id": tenant_id,
                "endpoint": endpoint,
                "connection_config": stored,
                "connection_secret_ref": secret_ref,
            },
        )
        assert hydrated["endpoint"] == full_endpoint
        assert hydrated["connection_config"] == full_config
        await session.commit()

    async with pg_engine.connect() as connection:
        ciphertext = (
            await connection.execute(
                text(
                    "SELECT ciphertext FROM encrypted_secrets "
                    "WHERE secret_id = :id"
                ),
                {"id": secret_ref},
            )
        ).scalar_one()
    assert "never-plaintext" not in ciphertext
