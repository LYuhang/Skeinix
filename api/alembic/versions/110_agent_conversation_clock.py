"""Persist user timezone and the immutable LangChain conversation clock.

Revision ID: 110
Revises: 109
Create Date: 2026-08-02
"""

from alembic import op


revision = "110"
down_revision = "109"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE user_agent_preferences ADD COLUMN IF NOT EXISTS "
        "preferred_timezone TEXT"
    )
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_timezone TEXT"
    )
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_started_at TIMESTAMPTZ"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE chats DROP COLUMN IF EXISTS runtime_started_at"
    )
    op.execute(
        "ALTER TABLE chats DROP COLUMN IF EXISTS runtime_timezone"
    )
    op.execute(
        "ALTER TABLE user_agent_preferences DROP COLUMN IF EXISTS "
        "preferred_timezone"
    )
