"""Encrypt credential-bearing LLM API/proxy URL components.

Revision ID: 064
Revises: 063
Create Date: 2026-07-31
"""

from alembic import op


revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS "
        "connection_secret_ref uuid REFERENCES encrypted_secrets(secret_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS "
        "connection_secret_version integer NOT NULL DEFAULT 1"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 064 is intentionally irreversible: LLM connection secrets "
        "cannot be restored to structural configuration"
    )
