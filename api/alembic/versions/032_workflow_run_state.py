"""workflow run state — lightweight interactive execution control plane.

Revision ID: 032
Revises: 031
Create Date: 2026-07-05

This is not an execution-history table. It stores one current interactive
workflow-page run per workflow plus an ordered event stream for refresh/replay.
Large outputs stay in the workflow run VFS.
"""
from alembic import op

revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_run_state (
            wf_id             TEXT PRIMARY KEY REFERENCES workflows(wf_id) ON DELETE CASCADE,
            tenant_id         UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                              DEFAULT current_setting('app.tenant_id', true)::uuid,
            creator_user_id   UUID NOT NULL REFERENCES users(user_id),
            turn_id           TEXT NOT NULL DEFAULT '',
            run_kind          TEXT NOT NULL DEFAULT 'workflow',
            status            TEXT NOT NULL DEFAULT 'running',
            target_node_id    TEXT,
            seq               INTEGER NOT NULL DEFAULT 0,
            node_states       JSONB NOT NULL DEFAULT '{}'::jsonb,
            cancel_requested  BOOLEAN NOT NULL DEFAULT false,
            error             TEXT,
            started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            ended_at          TIMESTAMPTZ,
            CONSTRAINT ck_workflow_run_state_kind
              CHECK (run_kind IN ('workflow','node')),
            CONSTRAINT ck_workflow_run_state_status
              CHECK (status IN ('pending','running','success','stopped','error'))
        );
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS workflow_run_events (
            wf_id       TEXT NOT NULL REFERENCES workflow_run_state(wf_id) ON DELETE CASCADE,
            seq         INTEGER NOT NULL,
            tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                        DEFAULT current_setting('app.tenant_id', true)::uuid,
            event_type  TEXT NOT NULL,
            payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (wf_id, seq)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_run_state_status "
        "ON workflow_run_state (tenant_id, status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_workflow_run_events_wf_seq "
        "ON workflow_run_events (wf_id, seq);"
    )
    for table in ("workflow_run_state", "workflow_run_events"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY workflow_run_state_tenant ON workflow_run_state FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);"
    )
    op.execute(
        "CREATE POLICY workflow_run_events_tenant ON workflow_run_events FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_run_state TO vibecanvas_app;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON workflow_run_events TO vibecanvas_app;")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS workflow_run_events_tenant ON workflow_run_events;")
    op.execute("DROP POLICY IF EXISTS workflow_run_state_tenant ON workflow_run_state;")
    op.execute("DROP TABLE IF EXISTS workflow_run_events CASCADE;")
    op.execute("DROP TABLE IF EXISTS workflow_run_state CASCADE;")
