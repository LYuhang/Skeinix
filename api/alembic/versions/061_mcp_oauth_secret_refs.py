"""Move MCP OAuth state and tokens behind SecretService references.

Revision ID: 061
Revises: 060
Create Date: 2026-07-31
"""

from alembic import op


revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE mcp_oauth_connections ADD COLUMN IF NOT EXISTS "
        "secret_ref uuid REFERENCES encrypted_secrets(secret_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE mcp_oauth_connections ADD COLUMN IF NOT EXISTS "
        "secret_version integer NOT NULL DEFAULT 1"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=current_schema() "
        "AND table_name='mcp_oauth_connections' "
        "AND column_name='access_token_encrypted') THEN "
        "ALTER TABLE mcp_oauth_connections "
        "ALTER COLUMN access_token_encrypted DROP NOT NULL; END IF; END $$"
    )
    op.execute(
        "ALTER TABLE mcp_oauth_transactions ADD COLUMN IF NOT EXISTS "
        "secret_ref uuid REFERENCES encrypted_secrets(secret_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE mcp_oauth_transactions ADD COLUMN IF NOT EXISTS "
        "secret_version integer NOT NULL DEFAULT 1"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=current_schema() "
        "AND table_name='mcp_oauth_transactions' "
        "AND column_name='code_verifier_encrypted') THEN "
        "ALTER TABLE mcp_oauth_transactions "
        "ALTER COLUMN code_verifier_encrypted DROP NOT NULL; END IF; END $$"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 061 is intentionally irreversible: OAuth secrets cannot "
        "be restored to retired Fernet columns"
    )
