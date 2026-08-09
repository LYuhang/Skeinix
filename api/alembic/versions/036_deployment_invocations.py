"""deployment invocation observability.

Revision ID: 036
Revises: 035
Create Date: 2026-07-11
"""
from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deployment_invocations (
            id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            deployment_id uuid NOT NULL REFERENCES deployments(id) ON DELETE CASCADE,
            wf_id text NOT NULL REFERENCES workflows(wf_id) ON DELETE CASCADE,
            trigger_type text NOT NULL,
            source text NOT NULL,
            status text NOT NULL,
            submitted_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz NULL,
            finished_at timestamptz NULL,
            latency_ms double precision NULL,
            error text NULL,
            result_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
            CONSTRAINT ck_deployment_invocations_trigger_type CHECK (
                trigger_type IN ('api', 'webhook')
            ),
            CONSTRAINT ck_deployment_invocations_source CHECK (
                source IN ('sync_api', 'async_api', 'webhook', 'test')
            ),
            CONSTRAINT ck_deployment_invocations_status CHECK (
                status IN ('queued', 'running', 'succeeded', 'failed')
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deployment_invocations_history
        ON deployment_invocations (tenant_id, deployment_id, submitted_at DESC, id DESC)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_deployment_invocations_metrics
        ON deployment_invocations (tenant_id, deployment_id, finished_at)
        WHERE finished_at IS NOT NULL
        """
    )
    op.execute("ALTER TABLE deployment_invocations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE deployment_invocations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON deployment_invocations
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON deployment_invocations TO vibecanvas_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS deployment_invocations")
