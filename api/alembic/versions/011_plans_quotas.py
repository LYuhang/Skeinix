"""plans & quotas — SEED the plan catalog. No DDL, no RLS.

Revision ID: 011
Revises: 010
Create Date: 2026-05-31

The `plans` table and the `tenants.plan_id` column (FK + server_default 'free' +
NOT NULL) are both created by migration 001's ``Base.metadata.create_all(bind)``
(they are in the models — ``Plan`` / ``Tenant.plan_id`` in storage/models.py).
This migration only does what create_all cannot: SEED the 3 catalog rows.

This is the FIRST seed-only (pure-data) migration in the repo (003-010 are all
DDL/RLS), so the INSERT idiom is spelled out rather than copied. The seed is
LOAD-BEARING for ALL tenant creation: because tenants.plan_id has the FK +
default 'free', every tenant insert (prod signup, every test) depends on the
'free' row existing. Migrations run before any tenant is created, so this holds.

`plans` is reference data: NOT tenant-scoped, NO RLS (every tenant may read the
catalog). vibecanvas_app owns it (create_all runs as vibecanvas_app), so SELECT
is owner-implicit — no GRANT needed.
"""
from alembic import op

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF to_regclass('public.plans') IS NOT NULL THEN
            INSERT INTO plans
              (plan_id, name, monthly_token_quota, monthly_exec_quota)
            VALUES
              ('free','Free',100000,50),
              ('pro','Pro',5000000,5000),
              ('enterprise','Enterprise',NULL,NULL)
            ON CONFLICT (plan_id) DO NOTHING;
          END IF;
        END
        $$
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF to_regclass('public.plans') IS NOT NULL THEN
            DELETE FROM plans WHERE plan_id IN ('free','pro','enterprise');
          END IF;
        END
        $$
    """)
