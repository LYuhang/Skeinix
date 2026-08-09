"""Require explicit structural owner metadata on shareable resources.

Revision ID: 074
Revises: 073
Create Date: 2026-07-31

Authorization decisions remain OpenFGA-only. These columns are durable source
metadata for projecting the initial manager relationship and may no longer
fall back to creator fields at runtime.
"""
from alembic import op


revision = "074"
down_revision = "073"
branch_labels = None
depends_on = None


def upgrade() -> None:
    sources = {
        "workflows": "creator_user_id",
        "templates": "creator_user_id",
        "tasks": "user_id",
        "deployments": "user_id",
    }
    for table, source in sources.items():
        op.execute(
            f"UPDATE {table} SET owner_id={source} WHERE owner_id IS NULL"
        )
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE "
            "owner_id IS NULL) THEN RAISE EXCEPTION "
            f"'{table} structural owner migration incomplete'; END IF; END $$"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN owner_id SET NOT NULL"
        )


def downgrade() -> None:
    raise RuntimeError(
        "revision 074 is intentionally irreversible: structural ownership "
        "must remain explicit"
    )
