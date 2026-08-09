"""Store only the user's non-secret managed Codex API selection.

Revision ID: 109
Revises: 108
Create Date: 2026-08-02
"""

from alembic import op


revision = "109"
down_revision = "108"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_agent_preferences ADD COLUMN IF NOT EXISTS "
        "codex_managed_profile_id TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE user_agent_preferences DROP COLUMN IF EXISTS "
        "codex_managed_profile_id"
    )
