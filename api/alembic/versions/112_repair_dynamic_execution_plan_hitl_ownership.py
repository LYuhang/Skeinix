"""Compatibility marker for retired Execution Plan HITL ownership.

Revision ID: 112
Revises: 111
Create Date: 2026-08-04

Revision 122 removes the former ownership columns from upgraded installations.
This marker remains so existing Alembic histories continue to advance safely.
"""

revision = "112"
down_revision = "111"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
