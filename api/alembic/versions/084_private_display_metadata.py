"""Add ciphertext storage for remaining private display metadata.

Revision ID: 084
Revises: 083
Create Date: 2026-07-31

Deployments pause here and run the strict content migrator before 085 removes
the plaintext columns. Runtime code never dual-reads these columns.
"""
from alembic import op


revision = "084"
down_revision = "083"
branch_labels = None
depends_on = None


def _content_key_fk(table: str, column: str, constraint: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
           WHERE tc.table_schema = current_schema()
             AND tc.table_name = '{table}'
             AND tc.constraint_type = 'FOREIGN KEY'
             AND kcu.column_name = '{column}'
          ) THEN
            ALTER TABLE {table} ADD CONSTRAINT {constraint}
              FOREIGN KEY ({column}) REFERENCES content_encryption_keys(key_id)
              ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def upgrade() -> None:
    for table in ("chats", "workflows"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "metadata_ciphertext text NOT NULL DEFAULT ''"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "metadata_nonce text NOT NULL DEFAULT ''"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS "
            "metadata_key_id uuid"
        )
        _content_key_fk(
            table,
            "metadata_key_id",
            f"fk_{table}_metadata_content_key",
        )

    op.execute(
        "ALTER TABLE workflow_versions ADD COLUMN IF NOT EXISTS "
        "note_ciphertext text NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE workflow_versions ADD COLUMN IF NOT EXISTS "
        "note_nonce text NOT NULL DEFAULT ''"
    )
    op.execute(
        "ALTER TABLE workflow_versions ADD COLUMN IF NOT EXISTS "
        "note_key_id uuid"
    )
    _content_key_fk(
        "workflow_versions",
        "note_key_id",
        "fk_workflow_versions_note_content_key",
    )
    op.execute(
        "ALTER TABLE task_schedules ADD COLUMN IF NOT EXISTS "
        "private_schema_version integer"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 084 is part of an intentionally irreversible plaintext "
        "metadata cutover"
    )
