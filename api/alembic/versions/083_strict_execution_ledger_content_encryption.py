"""Remove plaintext Agent Plan and execution-ledger content.

Revision ID: 083
Revises: 082
Create Date: 2026-07-31
"""
from alembic import op


revision = "083"
down_revision = "082"
branch_labels = None
depends_on = None


def _require_triplet(table: str, prefix: str, label: str) -> None:
    op.execute(
        f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE "
        f"{prefix}_ciphertext IS NULL OR {prefix}_nonce IS NULL OR "
        f"{prefix}_key_id IS NULL) THEN RAISE EXCEPTION '{label} content "
        "encryption migration incomplete'; END IF; END $$"
    )
    for suffix in ("ciphertext", "nonce", "key_id"):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {prefix}_{suffix} SET NOT NULL"
        )


def upgrade() -> None:
    _require_triplet("agent_plans", "private", "Agent Plan")
    _require_triplet("phase_events", "payload", "Phase event")
    _require_triplet("workflow_run_state", "private", "Workflow run state")
    _require_triplet("workflow_run_events", "payload", "Workflow run event")

    for column in ("title", "phases"):
        op.execute(f"ALTER TABLE agent_plans DROP COLUMN IF EXISTS {column}")
    op.execute("ALTER TABLE phase_events DROP COLUMN IF EXISTS payload")
    for column in ("node_states", "error"):
        op.execute(
            f"ALTER TABLE workflow_run_state DROP COLUMN IF EXISTS {column}"
        )
    op.execute("ALTER TABLE workflow_run_events DROP COLUMN IF EXISTS payload")


def downgrade() -> None:
    raise RuntimeError(
        "revision 083 is intentionally irreversible: execution ledger plaintext "
        "columns cannot be restored"
    )
