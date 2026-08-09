"""task resume statuses.

Revision ID: 034
Revises: 033
Create Date: 2026-07-10
"""
from alembic import op

revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_status")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_status "
        "CHECK (status IN ('queued', 'running', 'finished', 'failed', "
        "'cancelling', 'cancelled', 'finished_with_errors', "
        "'interrupted', 'resuming'))"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE tasks SET status='failed' "
        "WHERE status IN ('finished_with_errors', 'interrupted', 'resuming')"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_status")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_status "
        "CHECK (status IN ('queued', 'running', 'finished', 'failed', "
        "'cancelling', 'cancelled'))"
    )
