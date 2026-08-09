"""chats — add a ``meta`` JSONB column (default ``{}``, NOT NULL).

Revision ID: 027
Revises: 026
Create Date: 2026-06-19

The `/command` mode system (Design 2026-06-19) persists ``active_modes`` per
chat (sticky across turns / reopen). The least-invasive seam is a per-chat
``meta`` JSONB blob — mirroring ``chat_messages.meta`` — that holds
``{"active_modes": [...]}`` (and is free to carry future per-chat metadata).

Self-contained (does NOT rely on 001's create_all): ``ADD COLUMN IF NOT EXISTS``
is pure DDL — no row write — so even though ``chats`` is FORCE RLS, no RLS toggle
is needed for an added column (same lesson as migration 024). Idempotent on every
DB shape. The ``DEFAULT '{}'`` backfills existing rows and keeps the column NOT
NULL for new inserts that omit it.
"""
from alembic import op

revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS meta jsonb NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS meta")
