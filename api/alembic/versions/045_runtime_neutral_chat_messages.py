"""Give product chat messages stable runtime-neutral identities.

Revision ID: 045
Revises: 044
Create Date: 2026-07-21
"""

from alembic import op


revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS message_id TEXT")
    op.execute(
        "UPDATE chat_messages SET message_id='legacy_' || id::text "
        "WHERE message_id IS NULL"
    )
    op.execute("ALTER TABLE chat_messages ALTER COLUMN message_id SET NOT NULL")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_messages_message_id "
        "ON chat_messages(chat_id, message_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_chat_messages_message_id")
    op.execute("ALTER TABLE chat_messages DROP COLUMN IF EXISTS message_id")
