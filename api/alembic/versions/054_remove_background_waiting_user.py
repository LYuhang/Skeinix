"""Remove the unused background task-level HITL status.

Revision ID: 054
Revises: 053
Create Date: 2026-07-26
"""

from alembic import op


revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No executor ever produced waiting_user in V1. If a development database
    # contains such a row, close it explicitly rather than leaving an active
    # task that no longer has a valid transition.
    op.execute("ALTER TABLE chat_tool_jobs NO FORCE ROW LEVEL SECURITY")
    # Migration 001 historically builds from current Base metadata on a fresh
    # database. After the strict ciphertext cutover that means this historical
    # revision can encounter the new schema, where error_json has already been
    # removed. Keep bootstrap idempotent without recreating or dual-reading the
    # plaintext column.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = 'chat_tool_jobs'
                  AND column_name = 'error_json'
            ) THEN
                UPDATE chat_tool_jobs
                SET
                    status = 'failed',
                    error_json = jsonb_build_object(
                        'code', 'background_user_input_not_supported',
                        'message', 'Background tasks must run independently to a terminal state.'
                    ),
                    finished_at = COALESCE(finished_at, now()),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE status = 'waiting_user';
            ELSE
                UPDATE chat_tool_jobs
                SET
                    status = 'failed',
                    finished_at = COALESCE(finished_at, now()),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE status = 'waiting_user';
            END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE chat_tool_jobs "
        "DROP CONSTRAINT IF EXISTS ck_chat_tool_jobs_status"
    )
    op.execute(
        """
        ALTER TABLE chat_tool_jobs
        ADD CONSTRAINT ck_chat_tool_jobs_status
        CHECK (
            status IN (
                'queued','running','cancelling',
                'completed','failed','cancelled'
            )
        )
        """
    )
    op.execute("ALTER TABLE chat_tool_jobs FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    op.execute(
        "ALTER TABLE chat_tool_jobs "
        "DROP CONSTRAINT IF EXISTS ck_chat_tool_jobs_status"
    )
    op.execute(
        """
        ALTER TABLE chat_tool_jobs
        ADD CONSTRAINT ck_chat_tool_jobs_status
        CHECK (
            status IN (
                'queued','running','waiting_user','cancelling',
                'completed','failed','cancelled'
            )
        )
        """
    )
