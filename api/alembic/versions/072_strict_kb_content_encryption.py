"""Remove all plaintext Knowledge Base private content and vectors.

Revision ID: 072
Revises: 071
Create Date: 2026-07-31
"""
from alembic import op


revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def _require_envelope(table: str, prefix: str) -> None:
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE "
        f"{prefix}_key_id IS NULL OR {prefix}_ciphertext IS NULL "
        f"OR {prefix}_ciphertext='' OR {prefix}_nonce IS NULL "
        f"OR {prefix}_nonce='') THEN RAISE EXCEPTION "
        f"'{table} plaintext migration incomplete'; END IF; END $$"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {prefix}_ciphertext SET NOT NULL"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {prefix}_nonce SET NOT NULL"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {prefix}_key_id SET NOT NULL"
    )


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM knowledge_bases WHERE "
        "name_lookup_hash IS NULL OR name_lookup_hash='') THEN "
        "RAISE EXCEPTION 'knowledge_bases lookup migration incomplete'; "
        "END IF; END $$"
    )
    _require_envelope("knowledge_bases", "private")
    _require_envelope("kb_files", "private")
    _require_envelope("kb_chunks", "content")
    op.execute(
        "ALTER TABLE knowledge_bases ALTER COLUMN name_lookup_hash SET NOT NULL"
    )

    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_vector")
    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_metadata_gin")
    op.execute("DROP INDEX IF EXISTS uq_kb_tenant_name_active")
    for column in ("name", "description"):
        op.execute(
            f"ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS {column}"
        )
    for column in ("name", "error_message"):
        op.execute(f"ALTER TABLE kb_files DROP COLUMN IF EXISTS {column}")
    for column in ("text", "chunk_metadata", "embedding"):
        op.execute(f"ALTER TABLE kb_chunks DROP COLUMN IF EXISTS {column}")
    op.execute(
        "CREATE UNIQUE INDEX uq_kb_tenant_name_active "
        "ON knowledge_bases (tenant_id, name_lookup_hash) "
        "WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 072 is intentionally irreversible: Knowledge Base plaintext "
        "columns and vectors are not part of the current storage model"
    )
