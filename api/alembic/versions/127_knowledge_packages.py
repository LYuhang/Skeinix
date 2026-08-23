"""Make Knowledge a versioned hierarchical file package.

Revision ID: 127
Revises: 126
"""

from alembic import op
import sqlalchemy as sa


revision = "127"
down_revision = "126"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS "
        "package_version INTEGER NOT NULL DEFAULT 1"
    )
    # A package accepts arbitrary files. Parser support controls only whether a
    # derived text index is produced; it no longer controls durable storage.
    op.execute(
        "ALTER TABLE kb_files DROP CONSTRAINT IF EXISTS ck_kb_files_parser_type"
    )
    op.execute("DROP INDEX IF EXISTS uq_kb_files_kb_hash_active")
    op.execute(
        "ALTER TABLE kb_files DROP CONSTRAINT IF EXISTS ck_kb_files_status"
    )
    op.execute(
        "ALTER TABLE kb_files ADD CONSTRAINT ck_kb_files_status "
        "CHECK (status IN ('stored','pending','indexing','indexed','failed'))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM kb_files WHERE parser_type = 'binary'")
    op.execute("UPDATE kb_files SET status = 'failed' WHERE status = 'stored'")
    op.drop_constraint("ck_kb_files_status", "kb_files", type_="check")
    op.create_check_constraint(
        "ck_kb_files_status",
        "kb_files",
        "status IN ('pending','indexing','indexed','failed')",
    )
    op.create_index(
        "uq_kb_files_kb_hash_active",
        "kb_files",
        ["kb_id", "content_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_check_constraint(
        "ck_kb_files_parser_type",
        "kb_files",
        "parser_type IN ('pdf','markdown','txt','docx','pptx','xlsx','csv','json','html')",
    )
    op.execute(
        "ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS package_version"
    )
