"""Current Skill schema smoke tests — run against the conftest-migrated DB.

Migration 025 introduced Skills, while 047 replaced the legacy ``skill_files``
ObjectStore index with durable revision/draft tables. The fixture migrates to
HEAD, so these assertions deliberately verify the final converged schema.
"""
import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


async def test_skills_table_columns(pg_engine):
    async with pg_engine.connect() as conn:
        cols = {
            r[0]: r[1] for r in (await conn.execute(text(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'skills'"))).fetchall()
        }
    assert {
        "skill_id", "tenant_id", "user_id", "name", "description",
        "version", "allowed_tools", "created_at", "updated_at", "deleted_at",
        "current_revision_id",
    }.issubset(cols.keys())
    assert cols["allowed_tools"] == "jsonb"
    assert cols["version"] == "integer"


async def test_legacy_skill_files_table_is_replaced(pg_engine):
    async with pg_engine.connect() as conn:
        table = (await conn.execute(text(
            "SELECT to_regclass('public.skill_files')"
        ))).scalar_one()
    assert table is None


@pytest.mark.parametrize(
    ("table_name", "expected_columns"),
    [
        (
            "skill_revisions",
            {
                "revision_id", "skill_id", "tenant_id", "user_id",
                "revision_hash", "version", "file_manifest", "size_bytes",
            },
        ),
        (
            "skill_revision_files",
            {
                "revision_id", "skill_id", "tenant_id", "user_id", "path",
                "content_type", "content_hash", "size_bytes",
                "content_ciphertext", "content_nonce", "content_key_id",
            },
        ),
        (
            "skill_drafts",
            {
                "skill_id", "tenant_id", "user_id", "base_revision_id",
                "draft_hash", "file_manifest", "size_bytes", "updated_at",
            },
        ),
        (
            "skill_draft_files",
            {
                "skill_id", "tenant_id", "user_id", "path", "content_type",
                "content_hash", "size_bytes", "content_ciphertext",
                "content_nonce", "content_key_id",
            },
        ),
    ],
)
async def test_skill_revision_and_draft_table_columns(
    pg_engine, table_name, expected_columns
):
    async with pg_engine.connect() as conn:
        cols = {
            row[0]
            for row in (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :table_name"
                    ),
                    {"table_name": table_name},
                )
            ).fetchall()
        }
    assert expected_columns.issubset(cols)
    if table_name in {"skill_revision_files", "skill_draft_files"}:
        assert "content" not in cols


async def test_skill_user_partial_unique_index(pg_engine):
    async with pg_engine.connect() as conn:
        row = (await conn.execute(text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'skills' AND indexname = 'uq_skill_user_name'"
        ))).first()
    assert row is not None
    indexdef = row[0]
    assert "UNIQUE" in indexdef
    assert "user_id" in indexdef
    assert "deleted_at IS NULL" in indexdef


async def test_legacy_resource_grants_are_retired(pg_engine):
    async with pg_engine.connect() as conn:
        table = (
            await conn.execute(
                text("SELECT to_regclass('public.resource_grants')")
            )
        ).scalar_one()
    assert table is None
