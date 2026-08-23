"""Remove the retired scheduled Deployment variant.

Revision ID: 123
Revises: 122
Create Date: 2026-08-23

Recurring and calendar-based execution belongs to scheduled Tasks. A
Deployment is a published Workflow entry point and therefore supports only
API and webhook triggers.
"""

from alembic import op
import sqlalchemy as sa


revision = "123"
down_revision = "122"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Capture Deployment-owned service accounts before removing the retired
    # rows. The deployment FK uses RESTRICT, so the resource must be removed
    # first; credential links then cascade when an unreferenced account is
    # removed below.
    op.execute(
        """
        CREATE TEMPORARY TABLE retired_cron_deployment_accounts
        ON COMMIT DROP AS
        SELECT DISTINCT service_account_id
        FROM deployments
        WHERE trigger_type = 'cron' AND service_account_id IS NOT NULL
        """
    )
    op.execute("DELETE FROM deployments WHERE trigger_type = 'cron'")
    op.execute(
        """
        DELETE FROM service_accounts AS sa
        USING retired_cron_deployment_accounts AS retired
        WHERE sa.service_account_id = retired.service_account_id
          AND sa.owner_resource_type = 'deployment'
          AND NOT EXISTS (
              SELECT 1 FROM tasks AS t
              WHERE t.service_account_id = sa.service_account_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM task_schedules AS ts
              WHERE ts.service_account_id = sa.service_account_id
          )
          AND NOT EXISTS (
              SELECT 1 FROM deployments AS d
              WHERE d.service_account_id = sa.service_account_id
          )
        """
    )

    # Revision 001 intentionally reflects the current SQLAlchemy metadata on
    # fresh installs, so these legacy objects may already be absent by the
    # time the remaining migration chain reaches revision 123.
    op.execute(
        "ALTER TABLE deployments DROP CONSTRAINT IF EXISTS "
        "ck_deployments_cron_required"
    )
    op.execute(
        "ALTER TABLE deployments DROP CONSTRAINT IF EXISTS "
        "ck_deployments_trigger_type"
    )
    op.create_check_constraint(
        "ck_deployments_trigger_type",
        "deployments",
        "trigger_type IN ('api', 'webhook')",
    )
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS last_fire_at")
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS cron_tz")
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS cron_expr")


def downgrade() -> None:
    # The removed rows cannot be reconstructed. Downgrade restores only the
    # historical schema so an older application can create new Cron rows.
    op.add_column(
        "deployments",
        sa.Column("cron_expr", sa.Text(), nullable=True),
    )
    op.add_column(
        "deployments",
        sa.Column(
            "cron_tz",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'UTC'"),
        ),
    )
    op.add_column(
        "deployments",
        sa.Column("last_fire_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint(
        "ck_deployments_trigger_type", "deployments", type_="check"
    )
    op.create_check_constraint(
        "ck_deployments_trigger_type",
        "deployments",
        "trigger_type IN ('api', 'webhook', 'cron')",
    )
    op.create_check_constraint(
        "ck_deployments_cron_required",
        "deployments",
        "(trigger_type != 'cron') OR (cron_expr IS NOT NULL)",
    )
