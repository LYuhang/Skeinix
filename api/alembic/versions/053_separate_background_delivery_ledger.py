"""Separate background execution state from result delivery state.

Revision ID: 053
Revises: 052
Create Date: 2026-07-26
"""

from alembic import op


revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO vibecanvas_app"
    )


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_tool_job_deliveries (
            job_id text PRIMARY KEY
                REFERENCES chat_tool_jobs(job_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE
                DEFAULT current_setting('app.tenant_id', true)::uuid,
            chat_id text NOT NULL
                REFERENCES chats(chat_id) ON DELETE CASCADE,
            delivery_batch_id text NOT NULL,
            delivered_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_tool_job_deliveries_chat_batch "
        "ON chat_tool_job_deliveries "
        "(tenant_id, chat_id, delivery_batch_id)"
    )

    # Backfill the former execution-row delivery columns before removing them.
    # chat_tool_jobs uses FORCE RLS and migrations intentionally have no tenant
    # GUC, so temporarily let its owner read every historical tenant.
    op.execute("ALTER TABLE chat_tool_jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        INSERT INTO chat_tool_job_deliveries (
            job_id, tenant_id, chat_id, delivery_batch_id, delivered_at
        )
        SELECT
            job_id,
            tenant_id,
            chat_id,
            COALESCE(
                delivery_batch_id,
                'legacy_' || substr(md5(job_id), 1, 24)
            ),
            consumed_at
        FROM chat_tool_jobs
        WHERE consumed_at IS NOT NULL
        ON CONFLICT (job_id) DO NOTHING
        """
    )
    op.execute("ALTER TABLE chat_tool_jobs FORCE ROW LEVEL SECURITY")
    _rls("chat_tool_job_deliveries")
    op.execute("DROP INDEX IF EXISTS ix_chat_tool_jobs_pending_delivery")
    op.execute(
        "ALTER TABLE chat_tool_jobs "
        "DROP COLUMN IF EXISTS delivery_batch_id"
    )
    op.execute(
        "ALTER TABLE chat_tool_jobs DROP COLUMN IF EXISTS consumed_at"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE chat_tool_jobs "
        "ADD COLUMN IF NOT EXISTS consumed_at timestamptz"
    )
    op.execute(
        "ALTER TABLE chat_tool_jobs "
        "ADD COLUMN IF NOT EXISTS delivery_batch_id text"
    )
    op.execute("ALTER TABLE chat_tool_jobs NO FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        UPDATE chat_tool_jobs AS job
        SET
            consumed_at = delivery.delivered_at,
            delivery_batch_id = delivery.delivery_batch_id
        FROM chat_tool_job_deliveries AS delivery
        WHERE delivery.job_id = job.job_id
        """
    )
    op.execute("ALTER TABLE chat_tool_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_tool_jobs_pending_delivery "
        "ON chat_tool_jobs (tenant_id, chat_id, finished_at) "
        "WHERE consumed_at IS NULL AND "
        "status IN ('completed','failed','cancelled')"
    )
    op.execute("DROP TABLE IF EXISTS chat_tool_job_deliveries")
