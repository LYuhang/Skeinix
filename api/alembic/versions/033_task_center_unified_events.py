"""task center unified event protocol.

Revision ID: 033
Revises: 032
Create Date: 2026-07-10

Keep Task as the user-facing batch/schedule control plane. Deployment and
knowledge-base background work no longer use ``tasks`` rows.
"""
from alembic import op

revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM tasks "
        "WHERE task_type NOT IN ('batch_exec', 'scheduled_run')"
    )
    op.execute(
        "DELETE FROM task_events "
        "WHERE event_type NOT IN ('state', 'progress', 'log', 'result', 'terminal')"
    )

    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_task_type")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_task_type "
        "CHECK (task_type IN ('batch_exec', 'scheduled_run'))"
    )

    op.execute("ALTER TABLE task_events DROP CONSTRAINT IF EXISTS ck_task_events_event_type")
    op.execute(
        "ALTER TABLE task_events ADD CONSTRAINT ck_task_events_event_type "
        "CHECK (event_type IN ('state', 'progress', 'log', 'result', 'terminal'))"
    )

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_tasks_tenant_type_submitted "
        "ON tasks (tenant_id, task_type, submitted_at DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_task_events_task_id_id "
        "ON task_events (task_id, id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_task_events_task_id_id")
    op.execute("DROP INDEX IF EXISTS ix_tasks_tenant_type_submitted")
    op.execute("ALTER TABLE task_events DROP CONSTRAINT IF EXISTS ck_task_events_event_type")
    op.execute(
        "ALTER TABLE task_events ADD CONSTRAINT ck_task_events_event_type "
        "CHECK (event_type IN ('node_start', 'node_finish', 'progress', "
        "'log', 'error', 'finished', 'cancelled'))"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_task_type")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_task_type "
        "CHECK (task_type IN ('batch_exec', 'scheduled_run', 'webhook_run', "
        "'api_invoke_async', 'kb_index_file'))"
    )
