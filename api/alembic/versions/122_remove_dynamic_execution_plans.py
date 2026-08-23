"""Remove the retired Dynamic Execution Plan domain.

Revision ID: 122
Revises: 121
Create Date: 2026-08-22

The initial release no longer exposes ``/plan``. Codex-native plan progress and
the ordinary Chat Todo projection are separate Runtime concepts and remain.
"""

from alembic import op


revision = "122"
down_revision = "121"
branch_labels = None
depends_on = None


PLAN_TABLES_REVERSE_DEPENDENCY_ORDER = (
    "execution_plan_control_deliveries",
    "execution_plan_run_events",
    "execution_plan_events",
    "execution_plan_controls",
    "execution_node_outputs",
    "execution_node_attempts",
    "execution_node_runs",
    "execution_plan_runs",
    "execution_plan_revisions",
    "execution_plans",
)


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_hitl_requests_node_run_status")
    op.execute("DROP INDEX IF EXISTS ix_hitl_requests_plan_run_status")
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS "
        "ck_hitl_requests_exactly_one_owner"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS ck_hitl_requests_type"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS "
        "hitl_requests_execution_node_run_id_fkey"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP CONSTRAINT IF EXISTS "
        "hitl_requests_execution_plan_run_id_fkey"
    )
    # Plan-owned approval cards cannot be resumed once the feature is removed.
    op.execute("DELETE FROM hitl_requests WHERE run_id IS NULL")
    op.execute(
        "ALTER TABLE hitl_requests DROP COLUMN IF EXISTS execution_node_run_id"
    )
    op.execute(
        "ALTER TABLE hitl_requests DROP COLUMN IF EXISTS execution_plan_run_id"
    )
    op.create_check_constraint(
        "ck_hitl_requests_type",
        "hitl_requests",
        "hitl_type IN ('pre_tool_approval','post_tool_review','elicitation')",
    )
    op.create_check_constraint(
        "ck_hitl_requests_exactly_one_owner",
        "hitl_requests",
        "run_id IS NOT NULL",
    )
    for table in PLAN_TABLES_REVERSE_DEPENDENCY_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")


def downgrade() -> None:
    # The retired execution domain is intentionally not reconstructed. Historical
    # revisions 102 and 112 remain in the chain for upgrades from old installs.
    pass
