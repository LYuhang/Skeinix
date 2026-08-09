"""durable account purge jobs and expanded security audit taxonomy.

Revision ID: 066
Revises: 065
Create Date: 2026-07-31
"""
from alembic import op


revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


_ACTIONS = (
    "'auth.login_success','auth.login_failure','auth.logout','auth.register',"
    "'auth.password_reset_request','auth.password_reset_complete',"
    "'auth.session_list','auth.session_revoke','auth.session_rotate',"
    "'auth.account_delete_request','auth.account_delete_cancel',"
    "'deployment.key_rotate','mcp_server.credential_change',"
    "'deployment.create','deployment.delete','mcp_server.create',"
    "'mcp_server.delete','workflow.delete','kb.delete',"
    "'organization.create','organization.update','organization.member_change',"
    "'share.grant','share.revoke','service_account.create',"
    "'service_account.status_change','secret.create','secret.destroy',"
    "'purge.started','purge.completed','purge.failed'"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS data_purge_jobs (
            job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            deletion_request_id uuid NOT NULL UNIQUE
              REFERENCES account_deletion_requests(id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
            status text NOT NULL DEFAULT 'queued',
            current_phase text,
            completed_phases jsonb NOT NULL DEFAULT '[]'::jsonb,
            available_at timestamptz NOT NULL,
            lease_expires_at timestamptz,
            attempt_count integer NOT NULL DEFAULT 0,
            last_error_code text,
            last_error_message text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            started_at timestamptz,
            completed_at timestamptz,
            CONSTRAINT ck_data_purge_jobs_status CHECK (
              status IN ('queued','running','completed','failed','cancelled')
            ),
            CONSTRAINT ck_data_purge_jobs_attempt_count CHECK (attempt_count >= 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_data_purge_jobs_due "
        "ON data_purge_jobs(status, available_at, lease_expires_at)"
    )
    op.execute(
        "ALTER TABLE data_purge_jobs ALTER COLUMN job_id "
        "SET DEFAULT gen_random_uuid()"
    )
    # Migration 001 creates current ORM metadata on fresh installs. Older
    # checkouts may therefore already have this table with timezone-naive
    # columns from an intermediate model; normalize without shifting UTC data.
    for column in (
        "available_at", "lease_expires_at", "created_at", "updated_at",
        "started_at", "completed_at",
    ):
        op.execute(
            f"""
            DO $$
            BEGIN
              IF EXISTS (
                SELECT 1 FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'data_purge_jobs'
                   AND column_name = '{column}'
                   AND data_type = 'timestamp without time zone'
              ) THEN
                ALTER TABLE data_purge_jobs ALTER COLUMN {column}
                  TYPE timestamptz USING {column} AT TIME ZONE 'UTC';
              END IF;
            END $$
            """
        )
    op.execute(
        """
        INSERT INTO data_purge_jobs (
          deletion_request_id, user_id, tenant_id, status, available_at
        )
        SELECT id, user_id, tenant_id,
               CASE status WHEN 'cancelled' THEN 'cancelled'
                           WHEN 'purged' THEN 'completed'
                           ELSE 'queued' END,
               purge_after
          FROM account_deletion_requests
        ON CONFLICT (deletion_request_id) DO NOTHING
        """
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        f"ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action "
        f"CHECK (action IN ({_ACTIONS}))"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON data_purge_jobs TO vibecanvas_app")


def downgrade() -> None:
    old_actions = (
        "'auth.login_success','auth.login_failure','auth.logout','auth.register',"
        "'auth.password_reset_request','auth.password_reset_complete',"
        "'deployment.key_rotate','mcp_server.credential_change',"
        "'deployment.create','deployment.delete','mcp_server.create',"
        "'mcp_server.delete','workflow.delete','kb.delete'"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        f"ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action "
        f"CHECK (action IN ({old_actions}))"
    )
    op.execute("DROP INDEX IF EXISTS ix_data_purge_jobs_due")
    op.execute("DROP TABLE IF EXISTS data_purge_jobs")
