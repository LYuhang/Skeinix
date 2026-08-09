"""mcp normalized connection config and stdio transport.

Revision ID: 038
Revises: 037
Create Date: 2026-07-16
"""
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mcp_servers
        ADD COLUMN IF NOT EXISTS connection_config jsonb NOT NULL DEFAULT '{}'::jsonb
        """
    )
    op.execute("ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_transport")
    op.execute(
        """
        ALTER TABLE mcp_servers
        ADD CONSTRAINT ck_mcp_servers_transport
        CHECK (transport IN ('stdio', 'sse', 'streamable_http', 'streamable-http', 'http'))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_transport")
    op.execute(
        """
        ALTER TABLE mcp_servers
        ADD CONSTRAINT ck_mcp_servers_transport
        CHECK (transport IN ('sse', 'streamable_http'))
        """
    )
    op.execute("ALTER TABLE mcp_servers DROP COLUMN IF EXISTS connection_config")
