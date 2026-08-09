"""deployments — FORCE RLS + tenant policy + partial indexes + tasks CHECK update.

Revision ID: 005
Revises: 004

The ``deployments`` table, the new ``tasks.deployment_id`` /
``tasks.cluster_hint`` columns, and the new
``tenants.max_concurrent_deployments`` column are created by migration
001's ``Base.metadata.create_all(bind)`` — which mirrors the current
models. This migration only adds what ``create_all`` cannot express:

  - partial UNIQUE / partial regular indexes on ``deployments``
  - partial index on ``tasks(deployment_id, submitted_at DESC)``
  - row-level security (ENABLE + FORCE) + tenant_isolation policy on
    ``deployments``
  - ``tasks.task_type`` CHECK constraint update (drop + re-add with
    the new ``'api_invoke_async'`` value)

GRANTs are intentionally omitted — same reasoning as migration 004:
``create_all`` makes ``vibecanvas_app`` (the role running alembic in
tests + production) the OWNER of the new table, and owners hold DML
implicitly.
"""
from alembic import op

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade():
    # ----- Partial indexes on deployments -----
    op.execute(
        "CREATE UNIQUE INDEX ix_deployments_slug_tenant "
        "ON deployments (tenant_id, slug) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_deployments_tenant_enabled "
        "ON deployments (tenant_id, enabled) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_deployments_tenant_created "
        "ON deployments (tenant_id, created_at DESC) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX ix_deployments_trigger_type "
        "ON deployments (trigger_type, enabled) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_deployments_api_key_hash "
        "ON deployments (api_key_hash) "
        "WHERE api_key_hash IS NOT NULL AND deleted_at IS NULL"
    )

    # ----- Partial index on tasks for fast deployment-scoped history -----
    op.execute(
        "CREATE INDEX ix_tasks_deployment_submitted "
        "ON tasks (deployment_id, submitted_at DESC) "
        "WHERE deployment_id IS NOT NULL"
    )

    # ----- Update tasks.task_type CHECK to add 'api_invoke_async' -----
    # SQLAlchemy create_all writes the model's CURRENT CHECK constraint;
    # for a fresh DB the table already arrives with the 4-value CHECK.
    # But a previously migrated DB (e.g. existing test DB stuck at 004)
    # may carry the 3-value CHECK — so drop+add to make both paths idempotent.
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_task_type")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_task_type "
        "CHECK (task_type IN ('batch_exec', 'scheduled_run', 'webhook_run', "
        "'api_invoke_async'))"
    )

    # ----- RLS on deployments (Phase 5 / Phase 6 T2 pattern) -----
    op.execute("ALTER TABLE deployments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deployments FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON deployments FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )


def downgrade():
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON deployments")
    op.execute("ALTER TABLE deployments NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deployments DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tasks DROP CONSTRAINT IF EXISTS ck_tasks_task_type")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT ck_tasks_task_type "
        "CHECK (task_type IN ('batch_exec', 'scheduled_run', 'webhook_run'))"
    )
    op.execute("DROP INDEX IF EXISTS ix_tasks_deployment_submitted")
    op.execute("DROP INDEX IF EXISTS ix_deployments_api_key_hash")
    op.execute("DROP INDEX IF EXISTS ix_deployments_trigger_type")
    op.execute("DROP INDEX IF EXISTS ix_deployments_tenant_created")
    op.execute("DROP INDEX IF EXISTS ix_deployments_tenant_enabled")
    op.execute("DROP INDEX IF EXISTS ix_deployments_slug_tenant")
