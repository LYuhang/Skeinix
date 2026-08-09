"""Create the independent Dynamic Execution Plan execution domain.

Revision ID: 102
Revises: 101
Create Date: 2026-08-01
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "102"
down_revision = "101"
branch_labels = None
depends_on = None


TABLES = (
    "execution_plans",
    "execution_plan_revisions",
    "execution_plan_runs",
    "execution_node_runs",
    "execution_node_attempts",
    "execution_node_outputs",
    "execution_plan_controls",
    "execution_plan_events",
    "execution_plan_run_events",
    "execution_plan_control_deliveries",
)


def upgrade() -> None:
    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)
    # Some test/bootstrap paths create tables from current ORM metadata before
    # replaying migrations. Keep this upgrade safe in both that case and the
    # ordinary upgrade from revision 101.
    op.execute(
        "ALTER TABLE hitl_requests ADD COLUMN IF NOT EXISTS "
        "execution_plan_run_id TEXT"
    )
    op.execute(
        "ALTER TABLE hitl_requests ADD COLUMN IF NOT EXISTS "
        "execution_node_run_id TEXT"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE hitl_requests ADD CONSTRAINT "
        "hitl_requests_execution_plan_run_id_fkey FOREIGN KEY "
        "(execution_plan_run_id) REFERENCES execution_plan_runs(plan_run_id) "
        "ON DELETE CASCADE; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "ALTER TABLE hitl_requests ADD CONSTRAINT "
        "hitl_requests_execution_node_run_id_fkey FOREIGN KEY "
        "(execution_node_run_id) REFERENCES execution_node_runs(node_run_id) "
        "ON DELETE CASCADE; EXCEPTION WHEN duplicate_object THEN NULL; END $$"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS ck_hitl_requests_type"
    )
    op.create_check_constraint(
        "ck_hitl_requests_type",
        "hitl_requests",
        "hitl_type IN ('pre_tool_approval','post_tool_review','elicitation',"
        "'plan_start_approval','plan_node_tool_approval')",
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS "
        "ck_hitl_requests_exactly_one_owner"
    )
    op.create_check_constraint(
        "ck_hitl_requests_exactly_one_owner", "hitl_requests",
        "num_nonnulls(run_id, execution_plan_run_id, execution_node_run_id) = 1",
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hitl_requests_plan_run_status ON "
        "hitl_requests(tenant_id, execution_plan_run_id, status, created_at)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_hitl_requests_node_run_status ON "
        "hitl_requests(tenant_id, execution_node_run_id, status, created_at)"
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_plan_one_active_run
        ON execution_plan_runs(tenant_id, plan_id)
        WHERE status IN (
            'awaiting_approval','queued','running','cancel_requested'
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_execution_plan_control_tool_invocation
        ON execution_plan_controls(tenant_id, tool_invocation_id)
        WHERE tool_invocation_id IS NOT NULL
        """
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_hitl_requests_node_run_status")
    op.execute("DROP INDEX IF EXISTS ix_hitl_requests_plan_run_status")
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS "
        "ck_hitl_requests_exactly_one_owner"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS ck_hitl_requests_type"
    )
    op.create_check_constraint(
        "ck_hitl_requests_type",
        "hitl_requests",
        "hitl_type IN ('pre_tool_approval','post_tool_review','elicitation')",
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP COLUMN IF EXISTS execution_node_run_id"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP COLUMN IF EXISTS execution_plan_run_id"
    )
    for table in reversed(TABLES):
        op.drop_table(table)
