"""Remove plaintext Task and scheduled-run private documents.

Revision ID: 070
Revises: 069
Create Date: 2026-07-31
"""
from alembic import op


revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def _require_envelope(table: str, prefix: str) -> None:
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE "
        f"{prefix}_key_id IS NULL OR {prefix}_ciphertext IS NULL "
        f"OR {prefix}_ciphertext='' OR {prefix}_nonce IS NULL "
        f"OR {prefix}_nonce='') THEN RAISE EXCEPTION "
        f"'{table} plaintext migration incomplete'; END IF; END $$"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {prefix}_ciphertext SET NOT NULL"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {prefix}_nonce SET NOT NULL"
    )
    op.execute(
        f"ALTER TABLE {table} ALTER COLUMN {prefix}_key_id SET NOT NULL"
    )


def upgrade() -> None:
    _require_envelope("tasks", "content")
    _require_envelope("task_events", "payload")
    op.execute(
        "ALTER TABLE task_events ALTER COLUMN encryption_record_id SET NOT NULL"
    )
    _require_envelope("task_schedules", "private")
    _require_envelope("scheduled_run_executions", "private")

    op.execute("DROP INDEX IF EXISTS idx_tasks_kb_file_id")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS payload")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS result")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS error")
    op.execute("ALTER TABLE task_events DROP COLUMN IF EXISTS payload")
    op.execute("ALTER TABLE task_schedules DROP COLUMN IF EXISTS input_preset")
    op.execute(
        "ALTER TABLE task_schedules DROP COLUMN IF EXISTS notification_policy"
    )
    for column in (
        "input_snapshot", "result", "error", "run_state", "notification_state",
    ):
        op.execute(
            "ALTER TABLE scheduled_run_executions "
            f"DROP COLUMN IF EXISTS {column}"
        )


def downgrade() -> None:
    raise RuntimeError(
        "revision 070 is intentionally irreversible: Task plaintext columns "
        "are not part of the current storage model"
    )
