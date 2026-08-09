"""Enforce ciphertext-only storage for VFS abstracts.

Revision ID: 100
Revises: 099
Create Date: 2026-08-01
"""
from alembic import op


revision = "100"
down_revision = "099"
branch_labels = None
depends_on = None


def _make_strict(table: str) -> None:
    op.execute(
        f"""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM {table}
             WHERE abstract<>'' OR NOT (
               (abstract_key_id IS NULL AND abstract_ciphertext IS NULL
                AND abstract_nonce IS NULL) OR
               (abstract_key_id IS NOT NULL AND abstract_ciphertext IS NOT NULL
                AND abstract_ciphertext<>'' AND abstract_nonce IS NOT NULL
                AND abstract_nonce<>'')
             )
          ) THEN
            RAISE EXCEPTION
              '{table} abstract migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    op.execute(
        f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_abstract_private "
        "CHECK (abstract='' AND ((abstract_key_id IS NULL "
        "AND abstract_ciphertext IS NULL AND abstract_nonce IS NULL) OR "
        "(abstract_key_id IS NOT NULL AND abstract_ciphertext IS NOT NULL "
        "AND abstract_ciphertext<>'' AND abstract_nonce IS NOT NULL "
        "AND abstract_nonce<>'')))"
    )


def upgrade() -> None:
    for table in ("vfs_artifacts", "vfs_scratch", "vfs_run"):
        _make_strict(table)


def downgrade() -> None:
    raise RuntimeError(
        "revision 100 is intentionally irreversible: VFS abstracts must remain encrypted"
    )
