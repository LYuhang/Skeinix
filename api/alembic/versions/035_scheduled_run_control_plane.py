"""scheduled run control plane.

Revision ID: 035
Revises: 034
Create Date: 2026-07-10
"""
from alembic import op

revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_status")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_status "
        "CHECK (status IN ('queued', 'running', 'finished', 'failed', "
        "'cancelling', 'cancelled', 'finished_with_errors', "
        "'interrupted', 'resuming', 'enabled', 'paused'))"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS task_schedules (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(user_id),
            task_id uuid NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            workflow_id text NOT NULL REFERENCES workflows(wf_id) ON DELETE CASCADE,
            name text NOT NULL,
            enabled boolean NOT NULL DEFAULT true,
            schedule_type text NOT NULL,
            cron_expr text NULL,
            interval_seconds integer NULL,
            timezone text NOT NULL DEFAULT 'UTC',
            input_preset jsonb NOT NULL DEFAULT '{}'::jsonb,
            mount_enabled boolean NOT NULL DEFAULT false,
            notification_policy jsonb NOT NULL DEFAULT '{}'::jsonb,
            concurrency_policy text NOT NULL DEFAULT 'skip_if_running',
            failure_policy text NOT NULL DEFAULT 'none',
            catchup_policy boolean NOT NULL DEFAULT false,
            next_run_at timestamptz NULL,
            end_at timestamptz NULL,
            last_run_at timestamptz NULL,
            last_status text NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_task_schedules_type CHECK (schedule_type IN ('interval', 'cron')),
            CONSTRAINT ck_task_schedules_concurrency_policy CHECK (concurrency_policy IN ('skip_if_running')),
            CONSTRAINT ck_task_schedules_failure_policy CHECK (failure_policy IN ('none')),
            CONSTRAINT uq_task_schedules_task_id UNIQUE (task_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS scheduled_run_executions (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            schedule_id uuid NOT NULL REFERENCES task_schedules(id) ON DELETE CASCADE,
            workflow_id text NOT NULL REFERENCES workflows(wf_id) ON DELETE CASCADE,
            run_key text NOT NULL,
            status text NOT NULL DEFAULT 'queued',
            trigger_type text NOT NULL,
            triggered_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz NULL,
            finished_at timestamptz NULL,
            input_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            result jsonb NULL,
            results_uri text NULL,
            error text NULL,
            run_state jsonb NOT NULL DEFAULT '{}'::jsonb,
            notification_state jsonb NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT ck_scheduled_run_executions_status CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'skipped')
            ),
            CONSTRAINT ck_scheduled_run_executions_trigger_type CHECK (
                trigger_type IN ('scheduled', 'manual')
            ),
            CONSTRAINT uq_scheduled_run_executions_run_key UNIQUE (schedule_id, run_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_task_schedules_due "
        "ON task_schedules (tenant_id, enabled, next_run_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_scheduled_run_executions_history "
        "ON scheduled_run_executions (tenant_id, schedule_id, triggered_at DESC)"
    )
    op.execute("ALTER TABLE task_schedules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_schedules FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scheduled_run_executions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE scheduled_run_executions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON task_schedules
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON scheduled_run_executions
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON task_schedules TO vibecanvas_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON scheduled_run_executions TO vibecanvas_app")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS scheduled_run_executions")
    op.execute("DROP TABLE IF EXISTS task_schedules")
    op.execute(
        "UPDATE tasks SET status='failed' WHERE status IN ('enabled', 'paused')"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_status")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_status "
        "CHECK (status IN ('queued', 'running', 'finished', 'failed', "
        "'cancelling', 'cancelled', 'finished_with_errors', "
        "'interrupted', 'resuming'))"
    )
