"""Remove retired database-backed Diagram draft state.

Revision ID: 121
Revises: 120
Create Date: 2026-08-15

Diagram authoring now runs as a credential-free stdio MCP inside each Chat
sandbox. Its source of truth is the ordinary ``/data/diagrams`` file persisted
by the generic Sandbox/VFS lifecycle, so no Diagram-specific database state is
retained.
"""

from alembic import op


revision = "121"
down_revision = "120"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``diagram_operation_transactions`` came from an earlier development
    # iteration and may exist on upgraded installations even though it is not
    # part of the current migration chain.
    op.execute("DROP TABLE IF EXISTS diagram_operation_transactions")
    op.execute("DROP TABLE IF EXISTS diagram_render_revisions")
    op.execute("DROP TABLE IF EXISTS diagram_drafts")


def downgrade() -> None:
    # The retired design is intentionally not recreated. Revision 113 is a
    # compatibility marker only, so downgrading preserves the ordinary-file
    # architecture.
    pass
