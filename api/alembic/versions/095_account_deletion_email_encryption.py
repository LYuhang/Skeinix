"""Add migration-only envelope columns for deletion email snapshots.

Revision ID: 095
Revises: 094
Create Date: 2026-08-01
"""
from alembic import op


revision = "095"
down_revision = "094"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE account_deletion_requests ADD COLUMN IF NOT EXISTS "
        "email_snapshot_ciphertext text"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests ADD COLUMN IF NOT EXISTS "
        "email_snapshot_nonce text"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests ADD COLUMN IF NOT EXISTS "
        "email_snapshot_key_id uuid"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "ALTER COLUMN email_snapshot_ciphertext DROP NOT NULL, "
        "ALTER COLUMN email_snapshot_nonce DROP NOT NULL, "
        "ALTER COLUMN email_snapshot_key_id DROP NOT NULL"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.table_schema=kcu.table_schema
           WHERE tc.table_schema=current_schema()
             AND tc.table_name='account_deletion_requests'
             AND tc.constraint_type='FOREIGN KEY'
             AND kcu.column_name='email_snapshot_key_id'
          ) THEN
            ALTER TABLE account_deletion_requests
              ADD CONSTRAINT fk_account_deletion_email_key
              FOREIGN KEY (email_snapshot_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 095 is part of an intentionally irreversible deletion PII cutover"
    )
