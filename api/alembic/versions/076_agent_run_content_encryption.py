"""Add migration-only ciphertext storage for Agent Run replay content.

Revision ID: 076
Revises: 075
Create Date: 2026-07-31

Deployments pause at this revision and run the strict content migrator.  The
application binary targets revision 077 and never reads the old columns.
"""
from alembic import op


revision = "076"
down_revision = "075"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in (
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS private_ciphertext text",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS private_nonce text",
        "ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS private_key_id uuid "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT",
        "ALTER TABLE agent_run_events ADD COLUMN IF NOT EXISTS "
        "payload_ciphertext text",
        "ALTER TABLE agent_run_events ADD COLUMN IF NOT EXISTS payload_nonce text",
        "ALTER TABLE agent_run_events ADD COLUMN IF NOT EXISTS payload_key_id uuid "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT",
    ):
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "revision 076 is a one-way Agent Run content encryption cutover"
    )
