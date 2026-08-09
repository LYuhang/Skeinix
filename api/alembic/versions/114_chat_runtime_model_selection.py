"""Keep each Chat's effective Runtime model stable across omitted selections.

Revision ID: 114
Revises: 113
Create Date: 2026-08-07
"""

from alembic import op


revision = "114"
down_revision = "113"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_model_id TEXT")
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_connection_id TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_connection_id")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_model_id")
