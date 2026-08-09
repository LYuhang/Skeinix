"""Add durable Chat-scoped asynchronous tool jobs.

Revision ID: 049
Revises: 048
Create Date: 2026-07-24
"""

from alembic import op


revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO vibecanvas_app"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_tool_jobs (
            job_id text PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                DEFAULT current_setting('app.tenant_id', true)::uuid,
            chat_id text NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            creator_user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            parent_run_id text REFERENCES agent_runs(run_id) ON DELETE SET NULL,
            runtime_type text NOT NULL,
            executor_type text NOT NULL,
            tool_name text NOT NULL,
            title text NOT NULL DEFAULT '',
            status text NOT NULL DEFAULT 'queued',
            progress_current integer NOT NULL DEFAULT 0,
            progress_total integer,
            progress_message text NOT NULL DEFAULT '',
            input_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            result_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
            result_ref text,
            error_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            execution_handle_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            idempotency_key text NOT NULL,
            event_seq bigint NOT NULL DEFAULT 0,
            lease_owner text,
            lease_expires_at timestamptz,
            cancel_requested_at timestamptz,
            consumed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            finished_at timestamptz,
            CONSTRAINT ck_chat_tool_jobs_runtime_type
                CHECK (runtime_type IN ('langchain','codex')),
            CONSTRAINT ck_chat_tool_jobs_status
                CHECK (status IN (
                    'queued','running','waiting_user','cancelling',
                    'completed','failed','cancelled'
                )),
            CONSTRAINT ck_chat_tool_jobs_progress
                CHECK (
                    progress_current >= 0 AND
                    (progress_total IS NULL OR progress_total >= progress_current)
                ),
            CONSTRAINT uq_chat_tool_jobs_chat_idempotency
                UNIQUE (chat_id, idempotency_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_tool_jobs_chat_created "
        "ON chat_tool_jobs (tenant_id, chat_id, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_tool_jobs_dispatch "
        "ON chat_tool_jobs (tenant_id, status, lease_expires_at)"
    )
    _rls("chat_tool_jobs")

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_tool_job_events (
            job_id text NOT NULL REFERENCES chat_tool_jobs(job_id) ON DELETE CASCADE,
            seq bigint NOT NULL,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                DEFAULT current_setting('app.tenant_id', true)::uuid,
            event_type text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (job_id, seq)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_tool_job_events_job_seq "
        "ON chat_tool_job_events (job_id, seq)"
    )
    _rls("chat_tool_job_events")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS chat_tool_job_events")
    op.execute("DROP TABLE IF EXISTS chat_tool_jobs")
