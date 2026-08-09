"""Add two-person, time-bounded privileged support control plane.

Revision ID: 104
Revises: 103
Create Date: 2026-08-01
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "104"
down_revision = "103"
branch_labels = None
depends_on = None

_PRIVILEGED_ACTIONS = (
    "'privileged_access.request','privileged_access.approve',"
    "'privileged_access.deny','privileged_access.activate',"
    "'privileged_access.use','privileged_access.revoke'"
)

_ACTIONS_103 = (
    "'auth.login_success','auth.login_failure','auth.logout','auth.register',"
    "'auth.password_reset_request','auth.password_reset_complete',"
    "'auth.session_list','auth.session_revoke','auth.session_rotate',"
    "'auth.account_delete_request','auth.account_delete_cancel',"
    "'auth.mfa_enroll','auth.mfa_challenge','auth.mfa_recovery',"
    "'auth.mfa_disable','deployment.key_rotate',"
    "'mcp_server.credential_change','deployment.create','deployment.delete',"
    "'mcp_server.create','mcp_server.delete','workflow.delete','kb.delete',"
    "'organization.create','organization.update','organization.member_change',"
    "'share.grant','share.revoke','service_account.create',"
    "'service_account.status_change','secret.create','secret.destroy',"
    "'purge.started','purge.completed','purge.failed'"
)


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["privileged_access_requests"].create(
        bind, checkfirst=True,
    )
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS "
        "privileged_access_request_id UUID"
    )
    # Migration 001 intentionally materializes the *current* ORM metadata on
    # a fresh installation, so a new database already has this FK while an
    # existing revision-103 database does not. Rebuild it idempotently to make
    # both supported paths converge on the same definition.
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "fk_sessions_privileged_access_request"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT "
        "fk_sessions_privileged_access_request FOREIGN KEY "
        "(privileged_access_request_id) REFERENCES privileged_access_requests"
        "(request_id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_audience"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_audience CHECK "
        "(audience IN ('web','extension','api','support'))"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "ck_sessions_support_scope"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_support_scope CHECK "
        "((audience = 'support') = "
        "(privileged_access_request_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE privileged_access_requests ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE privileged_access_requests FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "CREATE POLICY privileged_access_tenant_isolation "
        "ON privileged_access_requests USING "
        "(tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
        "WITH CHECK "
        "(tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE "
        "ON privileged_access_requests TO vibecanvas_app"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_103},{_PRIVILEGED_ACTIONS}))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM sessions WHERE audience = 'support'")
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "ck_sessions_support_scope"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "fk_sessions_privileged_access_request"
    )
    op.execute(
        "ALTER TABLE sessions DROP COLUMN IF EXISTS privileged_access_request_id"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_audience"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_audience CHECK "
        "(audience IN ('web','extension','api'))"
    )
    op.execute("DROP TABLE IF EXISTS privileged_access_requests")
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_103}))"
    )
