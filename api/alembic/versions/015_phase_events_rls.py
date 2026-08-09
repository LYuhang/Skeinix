"""phase_events table + FORCE RLS (SP-3b-1a — durable phase-event log).

Revision ID: 015
Revises: 014

Mirrors task_events: a durable, cursor-readable append-only stream of
background plan-phase output, keyed by (run_id, phase_id). FORCE RLS by
tenant (same pattern as agent_plans / task_events).
"""
from alembic import op

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent CREATE so existing (already-migrated) DBs get the table while
    # fresh DBs (001 create_all already made it) no-op. RLS is then forced.
    op.execute("""
        CREATE TABLE IF NOT EXISTS phase_events (
            id          BIGSERIAL PRIMARY KEY,
            run_id      TEXT NOT NULL,
            phase_id    TEXT NOT NULL,
            ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
            event_type  TEXT NOT NULL,
            payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
            tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id),
            CONSTRAINT ck_phase_events_event_type
                CHECK (event_type IN ('running', 'output', 'done', 'error'))
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_phase_events_run_id "
        "ON phase_events (run_id, id);"
    )
    op.execute("ALTER TABLE phase_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE phase_events FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON phase_events FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id',true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id',true)::uuid);")


def downgrade():
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON phase_events;")
    op.execute("DROP TABLE IF EXISTS phase_events CASCADE;")
