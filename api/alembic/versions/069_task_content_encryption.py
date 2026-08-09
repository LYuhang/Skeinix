"""Add migration-only Task control-plane ciphertext envelopes.

Revision ID: 069
Revises: 068
Create Date: 2026-07-31

The application ORM is already ciphertext-only. Existing installations pause
at this revision so the deployment migrator can encrypt legacy rows before the
irreversible revision 070 removes their plaintext columns.
"""
from alembic import op


revision = "069"
down_revision = "068"
branch_labels = None
depends_on = None


def _add_envelope(table: str, prefix: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_ciphertext text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_nonce text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_key_id uuid"
    )
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS ("
        "SELECT 1 FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name=kcu.constraint_name "
        "AND tc.table_schema=kcu.table_schema "
        "WHERE tc.table_schema=current_schema() "
        f"AND tc.table_name='{table}' AND tc.constraint_type='FOREIGN KEY' "
        f"AND kcu.column_name='{prefix}_key_id') THEN "
        f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{prefix}_key "
        f"FOREIGN KEY ({prefix}_key_id) REFERENCES content_encryption_keys(key_id) "
        "ON DELETE RESTRICT; END IF; END $$"
    )


def upgrade() -> None:
    _add_envelope("tasks", "content")
    _add_envelope("task_events", "payload")
    op.execute(
        "ALTER TABLE task_events ADD COLUMN IF NOT EXISTS "
        "encryption_record_id uuid"
    )
    op.execute(
        "UPDATE task_events SET encryption_record_id=gen_random_uuid() "
        "WHERE encryption_record_id IS NULL"
    )
    _add_envelope("task_schedules", "private")
    _add_envelope("scheduled_run_executions", "private")


def downgrade() -> None:
    raise RuntimeError(
        "revision 069 contains migration ciphertext and is not downgradable"
    )
