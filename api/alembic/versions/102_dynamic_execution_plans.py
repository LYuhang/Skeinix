"""Compatibility marker for the retired Dynamic Execution Plan domain.

Revision ID: 102
Revises: 101
Create Date: 2026-08-01

Older installations may already contain the former tables. Revision 122 drops
them. Keeping this revision as a no-op lets fresh installations replay the
published migration chain without importing removed ORM implementation code.
"""

revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
