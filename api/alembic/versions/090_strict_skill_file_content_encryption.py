"""Remove plaintext Skill revision and draft file bodies.

Revision ID: 090
Revises: 089
Create Date: 2026-08-01
"""
from alembic import op


revision = "090"
down_revision = "089"
branch_labels = None
depends_on = None


def _strict_cutover(table: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM {table}
             WHERE content_key_id IS NULL
                OR content_ciphertext IS NULL OR content_ciphertext = ''
                OR content_nonce IS NULL OR content_nonce = ''
          ) THEN
            RAISE EXCEPTION
              '{table} migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    for column in ("content_key_id", "content_ciphertext", "content_nonce"):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
        )
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_content_envelope "
        "CHECK (content_ciphertext <> '' AND content_nonce <> '')"
    )
    op.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS content")


def upgrade() -> None:
    for table in ("skill_revision_files", "skill_draft_files"):
        _strict_cutover(table)


def downgrade() -> None:
    raise RuntimeError(
        "revision 090 is intentionally irreversible: plaintext Skill file "
        "columns are not part of the current storage model"
    )
