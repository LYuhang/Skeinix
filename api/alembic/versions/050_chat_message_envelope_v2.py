"""Upgrade product chat messages to the versioned visibility envelope.

Revision ID: 050
Revises: 049
Create Date: 2026-07-25
"""

from alembic import op


revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema=current_schema()
               AND table_name='chat_messages' AND column_name='content'
          ) THEN
            EXECUTE $sql$
              UPDATE chat_messages
                 SET content = content || jsonb_build_object(
                   'schema_version', 2,
                   'message_type', CASE WHEN role = 'tool'
                     THEN 'tool_result' ELSE 'text' END,
                   'visibility', 'visible'
                 )
               WHERE COALESCE((content->>'schema_version')::integer, 0) < 2
            $sql$;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema=current_schema()
               AND table_name='chat_messages' AND column_name='content'
          ) THEN
            EXECUTE $sql$
              UPDATE chat_messages
                 SET content = content - 'schema_version'
                   - 'message_type' - 'visibility'
               WHERE content->>'schema_version' = '2'
                 AND content->>'message_type' <> 'control'
            $sql$;
          END IF;
        END $$
        """
    )
