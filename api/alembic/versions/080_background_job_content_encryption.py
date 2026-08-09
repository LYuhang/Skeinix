"""Add migration-only background job ciphertext storage.

Revision ID: 080
Revises: 079
Create Date: 2026-07-31
"""
from alembic import op


revision = "080"
down_revision = "079"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chat_tool_jobs ADD COLUMN IF NOT EXISTS "
        "private_ciphertext text"
    )
    op.execute(
        "ALTER TABLE chat_tool_jobs ADD COLUMN IF NOT EXISTS private_nonce text"
    )
    op.execute(
        "ALTER TABLE chat_tool_jobs ADD COLUMN IF NOT EXISTS private_key_id uuid "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE chat_tool_job_events ADD COLUMN IF NOT EXISTS "
        "payload_ciphertext text"
    )
    op.execute(
        "ALTER TABLE chat_tool_job_events ADD COLUMN IF NOT EXISTS "
        "payload_nonce text"
    )
    op.execute(
        "ALTER TABLE chat_tool_job_events ADD COLUMN IF NOT EXISTS "
        "payload_key_id uuid REFERENCES content_encryption_keys(key_id) "
        "ON DELETE RESTRICT"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 080 is a one-way background job content encryption cutover"
    )
