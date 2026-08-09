"""chats - add explicit surface field.

Revision ID: 030
Revises: 029
Create Date: 2026-07-01

Separates ordinary app Chat history from browser side-panel Chat history while
keeping both on the existing workflow-carried chat storage.
"""
from alembic import op

revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chats "
        "ADD COLUMN IF NOT EXISTS surface text NOT NULL DEFAULT 'chat'"
    )
    op.execute(
        "ALTER TABLE chats "
        "ADD CONSTRAINT chats_surface_valid "
        "CHECK (surface IN ('chat', 'browser')) "
        "NOT VALID"
    )
    op.execute("ALTER TABLE chats VALIDATE CONSTRAINT chats_surface_valid")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'chats' AND column_name = 'scope_id'
            ) THEN
                CREATE INDEX IF NOT EXISTS ix_chats_surface_scope_last_msg
                ON chats (surface, scope_id, last_message_at)
                WHERE deleted_at IS NULL;
            ELSE
                CREATE INDEX IF NOT EXISTS ix_chats_surface_wf_last_msg
                ON chats (surface, wf_id, last_message_at)
                WHERE deleted_at IS NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chats_surface_scope_last_msg")
    op.execute("DROP INDEX IF EXISTS ix_chats_surface_wf_last_msg")
    op.execute("ALTER TABLE chats DROP CONSTRAINT IF EXISTS chats_surface_valid")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS surface")
