"""Encrypt MCP header, environment, and URL-query credentials.

Revision ID: 063
Revises: 062
Create Date: 2026-07-31
"""

from alembic import op


revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS "
        "connection_secret_ref uuid REFERENCES encrypted_secrets(secret_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS "
        "connection_secret_version integer NOT NULL DEFAULT 1"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 063 is intentionally irreversible: MCP connection secrets "
        "cannot be restored to structural configuration"
    )
