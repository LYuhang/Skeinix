"""Enforce ciphertext-only storage for private Template content.

Revision ID: 092
Revises: 091
Create Date: 2026-08-01
"""
from alembic import op


revision = "092"
down_revision = "091"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM templates
             WHERE visibility = 'private'
               AND (
                    private_key_id IS NULL
                 OR private_ciphertext IS NULL OR private_ciphertext = ''
                 OR private_nonce IS NULL OR private_nonce = ''
               )
          ) THEN
            RAISE EXCEPTION
              'private Template migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE templates ADD CONSTRAINT "
        "ck_templates_private_content_storage CHECK (("
        "visibility = 'private' AND private_key_id IS NOT NULL "
        "AND private_ciphertext IS NOT NULL AND private_ciphertext <> '' "
        "AND private_nonce IS NOT NULL AND private_nonce <> '' "
        "AND node_type = '' AND function_type = 'null'::jsonb "
        "AND description = 'null'::jsonb "
        "AND agent_hint = '' AND display = '{}'::jsonb "
        "AND workflow = '{}'::jsonb AND tags = '[]'::jsonb "
        "AND preview_path IS NULL"
        ") OR (visibility = 'public' AND private_key_id IS NULL "
        "AND private_ciphertext IS NULL AND private_nonce IS NULL))"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 092 is intentionally irreversible: private Template content "
        "must remain ciphertext-only"
    )
