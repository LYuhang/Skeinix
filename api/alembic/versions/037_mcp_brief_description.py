"""mcp server brief descriptions.

Revision ID: 037
Revises: 036
Create Date: 2026-07-15
"""
from alembic import op

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE mcp_servers
        ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT '',
        ADD COLUMN IF NOT EXISTS description_source text NOT NULL DEFAULT 'fallback',
        ADD COLUMN IF NOT EXISTS description_model_id text NULL,
        ADD COLUMN IF NOT EXISTS description_generated_at timestamptz NULL,
        ADD COLUMN IF NOT EXISTS description_basis_hash text NULL
        """
    )
    op.execute(
        """
        ALTER TABLE mcp_servers
        DROP CONSTRAINT IF EXISTS ck_mcp_servers_description_source
        """
    )
    op.execute(
        """
        ALTER TABLE mcp_servers
        ADD CONSTRAINT ck_mcp_servers_description_source
        CHECK (
            description_source IN (
                'registry',
                'server_metadata',
                'synthesized',
                'user_edited',
                'ai_generated',
                'fallback'
            )
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_description_source"
    )
    op.execute(
        """
        ALTER TABLE mcp_servers
        DROP COLUMN IF EXISTS description_basis_hash,
        DROP COLUMN IF EXISTS description_generated_at,
        DROP COLUMN IF EXISTS description_model_id,
        DROP COLUMN IF EXISTS description_source,
        DROP COLUMN IF EXISTS description
        """
    )
