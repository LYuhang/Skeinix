"""tasks.task_type CHECK — add 'kb_index_file' value.

Revision ID: 008
Revises: 007
Create Date: 2026-05-27

Mirrors 005_deployments_rls.py:68-73's DROP+ADD pattern so the migration
is idempotent across both fresh-from-create_all DBs (the model's current
CHECK already lists the 5 values) and previously-migrated DBs (still on
the 4-value CHECK from 005).

Kept as a separate migration (not merged into 007) so the KB / RAG and
``tasks`` concerns stay independently revertible: ``downgrade 008`` rolls
back ONLY the CHECK extension while leaving the KB tables + RLS in place,
and ``downgrade 007`` rolls back ONLY the KB tables + extension.
"""
from alembic import op

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None

NEW_VALUES = ("batch_exec", "scheduled_run", "webhook_run",
              "api_invoke_async", "kb_index_file")
OLD_VALUES = ("batch_exec", "scheduled_run", "webhook_run",
              "api_invoke_async")


def upgrade():
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_task_type")
    vals = ", ".join(f"'{v}'" for v in NEW_VALUES)
    op.execute(
        f"ALTER TABLE tasks ADD CONSTRAINT ck_tasks_task_type "
        f"CHECK (task_type IN ({vals}))"
    )


def downgrade():
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_task_type")
    vals = ", ".join(f"'{v}'" for v in OLD_VALUES)
    op.execute(
        f"ALTER TABLE tasks ADD CONSTRAINT ck_tasks_task_type "
        f"CHECK (task_type IN ({vals}))"
    )
