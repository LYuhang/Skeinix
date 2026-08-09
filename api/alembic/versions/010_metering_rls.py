"""metering — usage_events + usage_rollup_daily FORCE RLS + tenant policies +
usage_events tenant_id GUC default. NO append-only trigger (unlike audit 009)
— the downsample sweeper must DELETE usage_events rows past the 30-day horizon.

Revision ID: 010
Revises: 009
Create Date: 2026-05-30

Both tables are created by migration 001's ``Base.metadata.create_all(bind)``
(the models — ``UsageEvent`` / ``UsageRollupDaily`` — register via
``storage/models.py``). This migration only adds the RLS layer that
``create_all`` cannot express (same RLS-only style as 003-009):

  - usage_events: tenant_id auto-fill default from ``app.tenant_id`` (the agent
    write path omits tenant_id; the GUC fills it), FORCE RLS + a single FOR ALL
    policy (usage has no NULL-tenant case, so one policy suffices).
  - usage_rollup_daily: FORCE RLS + a single FOR ALL policy. NO tenant_id
    default — the admin/superuser downsample sweeper writes tenant_id
    explicitly via GROUP BY.

The composite/uuid PKs use ``gen_random_uuid()`` (events) / explicit columns
(rollup) — no sequence, so no sequence GRANT is needed.
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Metering was removed in revision 118. Migration 001 materializes the
    # current model metadata, so fresh installs no longer have these historical
    # tables when this revision is replayed. Guard the old RLS setup while still
    # supporting databases that were created before the removal.
    op.execute("""
        DO $$
        BEGIN
          IF to_regclass('public.usage_events') IS NOT NULL THEN
            ALTER TABLE usage_events ALTER COLUMN tenant_id
              SET DEFAULT current_setting('app.tenant_id', true)::uuid;
            ALTER TABLE usage_events ENABLE ROW LEVEL SECURITY;
            ALTER TABLE usage_events FORCE ROW LEVEL SECURITY;
            CREATE POLICY usage_events_tenant ON usage_events FOR ALL
              USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
            GRANT SELECT, INSERT, UPDATE, DELETE ON usage_events TO vibecanvas_app;
          END IF;
          IF to_regclass('public.usage_rollup_daily') IS NOT NULL THEN
            ALTER TABLE usage_rollup_daily ENABLE ROW LEVEL SECURITY;
            ALTER TABLE usage_rollup_daily FORCE ROW LEVEL SECURITY;
            CREATE POLICY usage_rollup_tenant ON usage_rollup_daily FOR ALL
              USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
              WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
            GRANT SELECT, INSERT, UPDATE, DELETE ON usage_rollup_daily TO vibecanvas_app;
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF to_regclass('public.usage_rollup_daily') IS NOT NULL THEN
            DROP POLICY IF EXISTS usage_rollup_tenant ON usage_rollup_daily;
            ALTER TABLE usage_rollup_daily NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE usage_rollup_daily DISABLE ROW LEVEL SECURITY;
          END IF;
          IF to_regclass('public.usage_events') IS NOT NULL THEN
            DROP POLICY IF EXISTS usage_events_tenant ON usage_events;
            ALTER TABLE usage_events NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE usage_events DISABLE ROW LEVEL SECURITY;
            ALTER TABLE usage_events ALTER COLUMN tenant_id DROP DEFAULT;
          END IF;
        END
        $$
    """)
