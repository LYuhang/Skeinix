"""Repair Dynamic Execution Plan ownership columns on upgraded databases.

Revision ID: 112
Revises: 111
Create Date: 2026-08-04

Revision 102 defines these columns and constraints. Early development
databases could nevertheless be stamped past that revision before the HITL
ownership additions landed. A normal ``upgrade head`` cannot replay an
already-stamped revision, so reconcile the physical schema idempotently here.
"""

from alembic import op


revision = "112"
down_revision = "111"
branch_labels = None
depends_on = None


def upgrade() -> None:
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
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS "
        "ck_hitl_requests_exactly_one_owner"
    )
    op.create_check_constraint(
        "ck_hitl_requests_exactly_one_owner",
        "hitl_requests",
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


def downgrade() -> None:
    # Revision 102 already defines this logical schema. Downgrading the repair
    # marker must not remove columns that a healthy revision-111 database has.
    pass
