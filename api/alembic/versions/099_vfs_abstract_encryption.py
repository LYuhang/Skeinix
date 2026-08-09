"""Add migration-only envelope columns for VFS abstracts.

Revision ID: 099
Revises: 098
Create Date: 2026-08-01
"""
from alembic import op


revision = "099"
down_revision = "098"
branch_labels = None
depends_on = None


def _add_columns(table: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS abstract_ciphertext text"
    )
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS abstract_nonce text")
    op.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS abstract_key_id uuid")
    op.execute(
        f"""
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.table_schema=kcu.table_schema
           WHERE tc.table_schema=current_schema() AND tc.table_name='{table}'
             AND tc.constraint_type='FOREIGN KEY'
             AND kcu.column_name='abstract_key_id'
          ) THEN
            ALTER TABLE {table} ADD CONSTRAINT fk_{table}_abstract_key
              FOREIGN KEY (abstract_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def upgrade() -> None:
    for table in ("vfs_artifacts", "vfs_scratch", "vfs_run"):
        _add_columns(table)


def downgrade() -> None:
    raise RuntimeError(
        "revision 099 is part of an intentionally irreversible VFS metadata cutover"
    )
