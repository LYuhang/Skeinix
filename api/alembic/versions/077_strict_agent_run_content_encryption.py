"""Remove plaintext Agent Run and replay-event content.

Revision ID: 077
Revises: 076
Create Date: 2026-07-31
"""
from alembic import op


revision = "077"
down_revision = "076"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_runs WHERE "
        "private_ciphertext IS NULL OR private_nonce IS NULL OR "
        "private_key_id IS NULL) THEN RAISE EXCEPTION "
        "'Agent Run content encryption migration incomplete'; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM agent_run_events WHERE "
        "payload_ciphertext IS NULL OR payload_nonce IS NULL OR "
        "payload_key_id IS NULL) THEN RAISE EXCEPTION "
        "'Agent Run event encryption migration incomplete'; "
        "END IF; END $$"
    )
    for statement in (
        "ALTER TABLE agent_runs ALTER COLUMN private_ciphertext SET NOT NULL",
        "ALTER TABLE agent_runs ALTER COLUMN private_nonce SET NOT NULL",
        "ALTER TABLE agent_runs ALTER COLUMN private_key_id SET NOT NULL",
        "ALTER TABLE agent_run_events ALTER COLUMN payload_ciphertext SET NOT NULL",
        "ALTER TABLE agent_run_events ALTER COLUMN payload_nonce SET NOT NULL",
        "ALTER TABLE agent_run_events ALTER COLUMN payload_key_id SET NOT NULL",
        "ALTER TABLE agent_runs DROP COLUMN IF EXISTS input_snapshot",
        "ALTER TABLE agent_runs DROP COLUMN IF EXISTS error_message",
        "ALTER TABLE agent_run_events DROP COLUMN IF EXISTS payload",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "revision 077 is intentionally irreversible: Agent Run plaintext "
        "columns cannot be restored"
    )
