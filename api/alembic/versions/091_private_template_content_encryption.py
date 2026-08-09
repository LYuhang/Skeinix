"""Add migration-only ciphertext storage for private Template content.

Revision ID: 091
Revises: 090
Create Date: 2026-08-01

Public templates remain plaintext because they are intentionally readable
across tenant boundaries. Private templates use their owning tenant's content
key and are cut over by revision 092.
"""
from alembic import op


revision = "091"
down_revision = "090"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE templates ADD COLUMN IF NOT EXISTS private_ciphertext text"
    )
    op.execute(
        "ALTER TABLE templates ADD COLUMN IF NOT EXISTS private_nonce text"
    )
    op.execute(
        "ALTER TABLE templates ADD COLUMN IF NOT EXISTS private_key_id uuid"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
           WHERE tc.table_schema = current_schema()
             AND tc.table_name = 'templates'
             AND tc.constraint_type = 'FOREIGN KEY'
             AND kcu.column_name = 'private_key_id'
          ) THEN
            ALTER TABLE templates ADD CONSTRAINT fk_templates_private_key
              FOREIGN KEY (private_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 091 is part of an intentionally irreversible private "
        "Template content cutover"
    )
