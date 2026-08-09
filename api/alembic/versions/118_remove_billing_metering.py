"""Remove the retired subscription and local token-usage ledgers.

Revision ID: 118
Revises: 117
Create Date: 2026-08-10
"""

from alembic import op

revision = "118"
down_revision = "117"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tenants DROP COLUMN IF EXISTS plan_id")
    op.execute("DROP TABLE IF EXISTS usage_rollup_daily")
    op.execute("DROP TABLE IF EXISTS usage_events")
    op.execute("DROP TABLE IF EXISTS plans")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS plans (
          plan_id text PRIMARY KEY,
          name text NOT NULL,
          monthly_token_quota bigint,
          monthly_exec_quota integer
        );
        INSERT INTO plans
          (plan_id, name, monthly_token_quota, monthly_exec_quota)
        VALUES
          ('free','Free',100000,50),
          ('pro','Pro',5000000,5000),
          ('enterprise','Enterprise',NULL,NULL)
        ON CONFLICT (plan_id) DO NOTHING;
        ALTER TABLE tenants ADD COLUMN IF NOT EXISTS plan_id text;
        UPDATE tenants SET plan_id = 'free' WHERE plan_id IS NULL;
        ALTER TABLE tenants ALTER COLUMN plan_id SET DEFAULT 'free';
        ALTER TABLE tenants ALTER COLUMN plan_id SET NOT NULL;
        ALTER TABLE tenants ADD CONSTRAINT tenants_plan_id_fkey
          FOREIGN KEY (plan_id) REFERENCES plans(plan_id);

        CREATE TABLE IF NOT EXISTS usage_events (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          ts timestamptz NOT NULL DEFAULT now(),
          model text NOT NULL,
          prompt_tokens integer NOT NULL DEFAULT 0,
          completion_tokens integer NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS ix_usage_events_tenant_ts
          ON usage_events (tenant_id, ts);
        ALTER TABLE usage_events ALTER COLUMN tenant_id
          SET DEFAULT current_setting('app.tenant_id', true)::uuid;
        ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
        ALTER TABLE usage_events FORCE ROW LEVEL SECURITY;
        CREATE POLICY usage_events_tenant ON usage_events FOR ALL
          USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);

        CREATE TABLE IF NOT EXISTS usage_rollup_daily (
          tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          day date NOT NULL,
          model text NOT NULL,
          prompt_tokens bigint NOT NULL DEFAULT 0,
          completion_tokens bigint NOT NULL DEFAULT 0,
          PRIMARY KEY (tenant_id, day, model)
        );
        ALTER TABLE usage_rollup_daily ENABLE ROW LEVEL SECURITY;
        ALTER TABLE usage_rollup_daily FORCE ROW LEVEL SECURITY;
        CREATE POLICY usage_rollup_tenant ON usage_rollup_daily FOR ALL
          USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
    """)
