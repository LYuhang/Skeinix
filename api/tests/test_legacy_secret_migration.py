"""Strict secret cutover invariants at the current Alembic head.

Legacy values are handled only by the explicitly staged, offline deployment
migrator before revision 073. Runtime and current-head tests must never rebuild
or exercise the retired plaintext schema: doing so would make accidental
dual-read compatibility look supported.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from vibecanvas_api.security.migrate_legacy_secrets import legacy_secret_count
from vibecanvas_api.storage import db as storage_db


_RETIRED_COLUMNS = {
    ("llm_credentials", "api_key"),
    ("mcp_oauth_connections", "client_secret_encrypted"),
    ("mcp_oauth_connections", "access_token_encrypted"),
    ("mcp_oauth_connections", "refresh_token_encrypted"),
    ("mcp_oauth_transactions", "code_verifier_encrypted"),
    ("mcp_oauth_transactions", "client_secret_encrypted"),
    ("deployments", "hmac_secret"),
}


@pytest.mark.asyncio
async def test_current_schema_has_no_retired_secret_columns(pg_engine) -> None:
    async with pg_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name FROM "
                    "information_schema.columns WHERE "
                    "table_schema=current_schema() AND table_name = ANY(:tables)"
                ),
                {"tables": sorted({table for table, _ in _RETIRED_COLUMNS})},
            )
        ).all()
    present = {(str(table), str(column)) for table, column in rows}
    assert present.isdisjoint(_RETIRED_COLUMNS)


@pytest.mark.asyncio
async def test_current_schema_requires_secret_references(pg_engine) -> None:
    async with pg_engine.connect() as connection:
        nullable_rows = (
            await connection.execute(
                text(
                    "SELECT table_name, column_name, is_nullable FROM "
                    "information_schema.columns WHERE "
                    "table_schema=current_schema() AND "
                    "(table_name, column_name) IN ("
                    "('llm_credentials','secret_ref'),"
                    "('mcp_oauth_connections','secret_ref'),"
                    "('mcp_oauth_transactions','secret_ref'))"
                )
            )
        ).all()
        constraints = {
            str(value)
            for value in (
                await connection.execute(
                    text(
                        "SELECT conname FROM pg_constraint WHERE conname = ANY(:names)"
                    ),
                    {
                        "names": [
                            "ck_deployments_hmac_required",
                            "ck_mcp_auth_secret_reference",
                            "ck_mcp_connection_public_projection",
                            "ck_llm_connection_public_projection",
                        ]
                    },
                )
            ).scalars()
        }
    assert {
        (str(table), str(column), str(nullable))
        for table, column, nullable in nullable_rows
    } == {
        ("llm_credentials", "secret_ref", "NO"),
        ("mcp_oauth_connections", "secret_ref", "NO"),
        ("mcp_oauth_transactions", "secret_ref", "NO"),
    }
    assert constraints == {
        "ck_deployments_hmac_required",
        "ck_mcp_auth_secret_reference",
        "ck_mcp_connection_public_projection",
        "ck_llm_connection_public_projection",
    }


@pytest.mark.asyncio
async def test_current_head_contains_no_pending_legacy_secret(pg_engine, monkeypatch) -> None:
    monkeypatch.setattr(storage_db, "_admin_engine", pg_engine)
    assert await legacy_secret_count() == 0
