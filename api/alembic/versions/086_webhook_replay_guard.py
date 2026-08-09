"""Add a durable replay ledger for authenticated deployment webhooks.

Revision ID: 086
Revises: 085
Create Date: 2026-07-31
"""

from alembic import op


revision = "086"
down_revision = "085"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS deployment_webhook_receipts (
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            deployment_id uuid NOT NULL
                REFERENCES deployments(id) ON DELETE CASCADE,
            signature_digest text NOT NULL,
            invocation_id uuid NOT NULL,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, deployment_id, signature_digest),
            CONSTRAINT ck_deployment_webhook_signature_digest
                CHECK (signature_digest ~ '^[0-9a-f]{64}$'),
            CONSTRAINT ck_deployment_webhook_receipt_expiry
                CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_deployment_webhook_receipts_expiry "
        "ON deployment_webhook_receipts (expires_at)"
    )
    op.execute(
        "ALTER TABLE deployment_webhook_receipts ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE deployment_webhook_receipts FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation "
        "ON deployment_webhook_receipts"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON deployment_webhook_receipts
        USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON deployment_webhook_receipts "
        "TO vibecanvas_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS deployment_webhook_receipts")
