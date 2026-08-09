"""Remove legacy plaintext Chat and Workflow content columns.

Revision ID: 068
Revises: 067
Create Date: 2026-07-31

Deployments with existing rows must pause at 067, run the strict content
migrator, and only then advance to this revision.  The guard deliberately
fails instead of dropping the only copy of user content.
"""
from alembic import op


revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM chat_messages
             WHERE content_key_id IS NULL
                OR content_ciphertext IS NULL OR content_ciphertext = ''
                OR content_nonce IS NULL OR content_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'chat plaintext migration incomplete; run strict content migrator';
          END IF;
          IF EXISTS (
            SELECT 1 FROM workflow_versions
             WHERE workflow_key_id IS NULL
                OR workflow_ciphertext IS NULL OR workflow_ciphertext = ''
                OR workflow_nonce IS NULL OR workflow_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'workflow plaintext migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE chat_messages ALTER COLUMN content_ciphertext SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE chat_messages ALTER COLUMN content_nonce SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE chat_messages ALTER COLUMN content_key_id SET NOT NULL"
    )
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS content")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS meta")

    op.execute(
        "ALTER TABLE workflow_versions "
        "ALTER COLUMN workflow_ciphertext SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN workflow_nonce SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN workflow_key_id SET NOT NULL"
    )
    op.execute("ALTER TABLE workflow_versions DROP COLUMN IF EXISTS workflow")


def downgrade() -> None:
    raise RuntimeError(
        "revision 068 is intentionally irreversible: plaintext content "
        "columns are not part of the current storage model"
    )
