"""Move MCP bearer tokens behind SecretService references.

Revision ID: 060
Revises: 059
Create Date: 2026-07-31
"""

from alembic import op


revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS "
        "auth_secret_ref uuid REFERENCES encrypted_secrets(secret_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE mcp_servers ADD COLUMN IF NOT EXISTS "
        "auth_secret_version integer NOT NULL DEFAULT 1"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 060 is intentionally irreversible: bearer secrets cannot "
        "be restored to MCP configuration JSON"
    )
