"""agent_plans table + FORCE RLS (SP-2 Phase A).

Revision ID: 014
Revises: 013
"""
from alembic import op

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade():
    # Idempotent CREATE so existing (already-migrated) DBs get the table while
    # fresh DBs (001 create_all already made it) no-op. RLS is then forced.
    op.execute("""
        CREATE TABLE IF NOT EXISTS agent_plans (
            plan_id     TEXT PRIMARY KEY,
            run_id      TEXT NOT NULL,
            chat_id     TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            wf_id       TEXT NOT NULL DEFAULT '',
            title       TEXT NOT NULL DEFAULT '',
            status      TEXT NOT NULL DEFAULT 'planning',
            phases      JSONB NOT NULL DEFAULT '[]'::jsonb,
            creator_user_id UUID NOT NULL REFERENCES users(user_id),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id)
                        DEFAULT current_setting('app.tenant_id', true)::uuid
        );
    """)
    op.execute("ALTER TABLE agent_plans ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE agent_plans FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON agent_plans FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id',true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id',true)::uuid);")


def downgrade():
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON agent_plans;")
    op.execute("DROP TABLE IF EXISTS agent_plans CASCADE;")
