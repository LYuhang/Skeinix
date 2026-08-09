"""Add migration-only ciphertext storage for Skill file bodies.

Revision ID: 089
Revises: 088
Create Date: 2026-08-01

Deployments pause here and run the strict content migrator before revision 090
removes the plaintext ``content`` columns. Runtime code never dual-reads them.
"""
from alembic import op


revision = "089"
down_revision = "088"
branch_labels = None
depends_on = None


def _add_envelope(table: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS content_ciphertext text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS content_nonce text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS content_key_id uuid"
    )
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
             AND kcu.column_name = 'content_key_id'
          ) THEN
            ALTER TABLE {table} ADD CONSTRAINT fk_{table}_content_key
              FOREIGN KEY (content_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def upgrade() -> None:
    for table in ("skill_revision_files", "skill_draft_files"):
        _add_envelope(table)


def downgrade() -> None:
    raise RuntimeError(
        "revision 089 is part of an intentionally irreversible Skill content "
        "cutover"
    )
