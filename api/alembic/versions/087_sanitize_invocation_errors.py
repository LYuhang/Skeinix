"""Remove legacy free-form deployment error text.

Revision ID: 087
Revises: 086
Create Date: 2026-07-31
"""
from alembic import op


revision = "087"
down_revision = "086"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Invocation history is operational metadata. Free-form engine exception
    # text may contain workflow inputs/outputs, so retain only stable codes.
    op.execute(
        "UPDATE deployment_invocations SET error='execution_failed' "
        "WHERE error IS NOT NULL "
        "AND error !~ '^[a-z0-9_.:-]{1,128}$'"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 087 is intentionally irreversible: free-form error text "
        "cannot be reconstructed"
    )
