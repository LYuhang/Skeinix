"""Migration 024 smoke tests — run against the conftest-migrated DB.

The conftest ``_migrate`` fixture runs ``alembic upgrade head`` (so migration
024 has already executed) and ``pg_engine`` is a superuser async engine to that
DB. These assert the schema artifacts migration 024 owns: the ``vfs_run.wf_id``
column (nullable, text) + the ``(tenant_id, wf_id)`` index.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_024_vfs_run_wf_id_column(pg_engine):
    async with pg_engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'vfs_run' AND column_name = 'wf_id'"))).first()
    assert row is not None
    assert row[0] == "text"
    assert row[1] == "YES"   # nullable (back-compat for existing rows)


async def test_024_vfs_run_wf_index_exists(pg_engine):
    async with pg_engine.connect() as conn:
        names = {r[0] for r in (await conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'vfs_run'"
        ))).fetchall()}
    assert "ix_vfs_run_wf" in names
