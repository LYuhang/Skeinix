"""Remove plaintext background job content.

Revision ID: 081
Revises: 080
Create Date: 2026-07-31
"""
from alembic import op


revision = "081"
down_revision = "080"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM chat_tool_jobs WHERE "
        "private_ciphertext IS NULL OR private_nonce IS NULL OR "
        "private_key_id IS NULL) THEN RAISE EXCEPTION 'background job content "
        "encryption migration incomplete'; END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM chat_tool_job_events WHERE "
        "payload_ciphertext IS NULL OR payload_nonce IS NULL OR "
        "payload_key_id IS NULL) THEN RAISE EXCEPTION 'background job event "
        "encryption migration incomplete'; END IF; END $$"
    )
    for column in ("private_ciphertext", "private_nonce", "private_key_id"):
        op.execute(
            f"ALTER TABLE chat_tool_jobs ALTER COLUMN {column} SET NOT NULL"
        )
    for column in ("payload_ciphertext", "payload_nonce", "payload_key_id"):
        op.execute(
            f"ALTER TABLE chat_tool_job_events ALTER COLUMN {column} SET NOT NULL"
        )

    for column in (
        "title",
        "progress_message",
        "input_snapshot",
        "result_snapshot",
        "result_ref",
        "error_json",
        "execution_handle_json",
    ):
        op.execute(
            f"ALTER TABLE chat_tool_jobs DROP COLUMN IF EXISTS {column}"
        )
    op.execute(
        "ALTER TABLE chat_tool_job_events DROP COLUMN IF EXISTS payload"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 081 is intentionally irreversible: background job plaintext "
        "columns cannot be restored"
    )
