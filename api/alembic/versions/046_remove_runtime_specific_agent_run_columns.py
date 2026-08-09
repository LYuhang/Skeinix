"""Keep Agent Run storage independent from Runtime checkpoint schemas.

Revision ID: 046
Revises: 045
Create Date: 2026-07-21
"""

from alembic import op


revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS checkpoint_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS base_checkpoint_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS result_checkpoint_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS thread_id")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS thread_id TEXT")
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS checkpoint_id TEXT")
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS base_checkpoint_id TEXT")
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS result_checkpoint_id TEXT")
