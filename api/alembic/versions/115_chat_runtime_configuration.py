"""Freeze non-secret Runtime settings with each Chat.

Revision ID: 115
Revises: 114
Create Date: 2026-08-07
"""

from alembic import op


revision = "115"
down_revision = "114"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_agent_settings JSONB"
    )
    # `codex:default` was the old implicit bridge to config.agent. It no longer
    # exists; release those legacy placeholders so the next Turn can bind an
    # explicitly configured account, managed API, or personal API model.
    op.execute(
        """
        UPDATE chats
           SET runtime_model_id = NULL,
               runtime_connection_id = NULL,
               runtime_agent_settings = NULL
         WHERE runtime_model_id = 'codex:default'
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_agent_settings")
