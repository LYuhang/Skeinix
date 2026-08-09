"""Remove Knowledge embedding metadata after Agent-native retrieval cutover.

Revision ID: 116
Revises: 115
Create Date: 2026-08-07
"""
from alembic import op


revision = "116"
down_revision = "115"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS embedding_model")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS embedding_dim")
    op.execute("ALTER TABLE kb_chunks DROP COLUMN IF EXISTS embedding_model")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS "
        "embedding_model text NOT NULL DEFAULT 'local-hash-v1'"
    )
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS "
        "embedding_dim integer NOT NULL DEFAULT 1536"
    )
    op.execute(
        "ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS "
        "embedding_model text NOT NULL DEFAULT 'local-hash-v1'"
    )
