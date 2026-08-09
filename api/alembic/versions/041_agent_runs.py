"""Durable agent run control plane and resumable UI event log.

Revision ID: 041
Revises: 040
Create Date: 2026-07-17
"""
from alembic import op


revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_runs (
            run_id               TEXT PRIMARY KEY,
            tenant_id            UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                                 DEFAULT current_setting('app.tenant_id', true)::uuid,
            chat_id              TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            creator_user_id      UUID NOT NULL REFERENCES users(user_id),
            thread_id            TEXT NOT NULL,
            client_request_id    TEXT NOT NULL,
            status               TEXT NOT NULL DEFAULT 'running',
            checkpoint_id        TEXT,
            last_event_id        BIGINT NOT NULL DEFAULT 0,
            input_snapshot       JSONB NOT NULL DEFAULT '{}'::jsonb,
            cancel_requested_at  TIMESTAMPTZ,
            heartbeat_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            error_code           TEXT,
            error_message        TEXT,
            created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at             TIMESTAMPTZ,
            CONSTRAINT ck_agent_runs_status CHECK (
                status IN (
                    'running', 'waiting_approval', 'cancel_requested',
                    'completed', 'cancelled', 'failed'
                )
            ),
            CONSTRAINT uq_agent_runs_chat_request UNIQUE (chat_id, client_request_id)
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_chat
        ON agent_runs (chat_id)
        WHERE status IN ('running', 'waiting_approval', 'cancel_requested')
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_runs_tenant_chat_created
        ON agent_runs (tenant_id, chat_id, created_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_runs_tenant_status_heartbeat
        ON agent_runs (tenant_id, status, heartbeat_at)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_run_events (
            run_id       TEXT NOT NULL REFERENCES agent_runs(run_id) ON DELETE CASCADE,
            seq          BIGINT NOT NULL,
            tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                         DEFAULT current_setting('app.tenant_id', true)::uuid,
            event_type   TEXT NOT NULL,
            payload      JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (run_id, seq)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_agent_run_events_run_seq
        ON agent_run_events (run_id, seq)
    """)

    for table in ("agent_runs", "agent_run_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO vibecanvas_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_run_events")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_runs")
    op.execute("DROP TABLE IF EXISTS agent_run_events CASCADE")
    op.execute("DROP TABLE IF EXISTS agent_runs CASCADE")
