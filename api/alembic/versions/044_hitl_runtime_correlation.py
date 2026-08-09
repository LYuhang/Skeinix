"""Persist runtime-native correlation for resumable HITL requests.

Revision ID: 044
Revises: 043
Create Date: 2026-07-21
"""

from alembic import op


revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE hitl_requests ADD COLUMN IF NOT EXISTS "
        "runtime_correlation_json JSONB NOT NULL DEFAULT '{}'::jsonb"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE hitl_requests DROP COLUMN IF EXISTS runtime_correlation_json")
