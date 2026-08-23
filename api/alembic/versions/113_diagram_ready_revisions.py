"""Reserved compatibility marker for a retired Diagram draft design.

Revision ID: 113
Revises: 112
Create Date: 2026-08-06

The unreleased database-backed draft implementation was removed before the
public Diagram contract stabilized. Keep the revision identifier so existing
development databases retain a valid Alembic chain; fresh installations must
not create Diagram-specific tables.
"""


revision = "113"
down_revision = "112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
