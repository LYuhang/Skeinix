"""Add migration-only HITL and Interactive Artifact ciphertext storage.

Revision ID: 078
Revises: 077
Create Date: 2026-07-31
"""
from alembic import op


revision = "078"
down_revision = "077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("hitl_requests", "interactive_artifacts"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS private_ciphertext text"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS private_nonce text"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS private_key_id uuid "
            "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT"
        )


def downgrade() -> None:
    raise RuntimeError(
        "revision 078 is a one-way HITL content encryption cutover"
    )
