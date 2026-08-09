"""Make public deployment slugs globally unambiguous.

Revision ID: 088
Revises: 087
Create Date: 2026-07-31
"""
from alembic import op


revision = "088"
down_revision = "087"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM deployments
             WHERE deleted_at IS NULL
             GROUP BY slug HAVING count(*) > 1
          ) THEN
            RAISE EXCEPTION
              'active deployment slug collision; rename before revision 088';
          END IF;
        END $$
        """
    )
    op.execute("DROP INDEX IF EXISTS ix_deployments_slug_tenant")
    op.execute(
        "CREATE UNIQUE INDEX ix_deployments_slug_global "
        "ON deployments (slug) WHERE deleted_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_deployments_slug_global")
    op.execute(
        "CREATE UNIQUE INDEX ix_deployments_slug_tenant "
        "ON deployments (tenant_id, slug) WHERE deleted_at IS NULL"
    )
