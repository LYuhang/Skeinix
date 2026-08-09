"""Skill catalog source metadata.

Revision ID: 039
Revises: 038
Create Date: 2026-07-17
"""
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE skills
        ADD COLUMN IF NOT EXISTS source text NULL,
        ADD COLUMN IF NOT EXISTS source_id text NULL,
        ADD COLUMN IF NOT EXISTS source_url text NULL,
        ADD COLUMN IF NOT EXISTS source_revision text NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_skills_tenant_source_live
        ON skills (tenant_id, source, source_id)
        WHERE deleted_at IS NULL AND source IS NOT NULL AND source_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_skills_tenant_source_live")
    op.execute(
        """
        ALTER TABLE skills
        DROP COLUMN IF EXISTS source_revision,
        DROP COLUMN IF EXISTS source_url,
        DROP COLUMN IF EXISTS source_id,
        DROP COLUMN IF EXISTS source
        """
    )
