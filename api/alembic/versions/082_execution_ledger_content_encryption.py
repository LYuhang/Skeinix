"""Add migration-only Agent Plan and execution-ledger ciphertext storage.

Revision ID: 082
Revises: 081
Create Date: 2026-07-31
"""
from alembic import op


revision = "082"
down_revision = "081"
branch_labels = None
depends_on = None


def _add_private_triplet(table: str, prefix: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_ciphertext text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_nonce text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_key_id uuid "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT"
    )


def upgrade() -> None:
    for table in ("agent_plans", "workflow_run_state"):
        _add_private_triplet(table, "private")
    for table in ("phase_events", "workflow_run_events"):
        _add_private_triplet(table, "payload")


def downgrade() -> None:
    raise RuntimeError(
        "revision 082 is a one-way execution ledger content encryption cutover"
    )
