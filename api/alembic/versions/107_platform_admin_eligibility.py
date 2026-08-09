"""Add reviewed and expiring platform support eligibility.

Revision ID: 107
Revises: 106
Create Date: 2026-08-02
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "107"
down_revision = "106"
branch_labels = None
depends_on = None

_ACTIONS_106 = (
    "'auth.login_success','auth.login_failure','auth.logout','auth.register',"
    "'auth.password_reset_request','auth.password_reset_complete',"
    "'auth.session_list','auth.session_revoke','auth.session_rotate',"
    "'auth.account_delete_request','auth.account_delete_cancel',"
    "'auth.mfa_enroll','auth.mfa_challenge','auth.mfa_recovery',"
    "'auth.mfa_disable','auth.sso_login_success','auth.sso_login_failure',"
    "'deployment.key_rotate','mcp_server.credential_change',"
    "'deployment.create','deployment.delete','mcp_server.create',"
    "'mcp_server.delete','workflow.delete','kb.delete',"
    "'organization.create','organization.update','organization.member_change',"
    "'share.grant','share.revoke','service_account.create',"
    "'service_account.status_change','secret.create','secret.destroy',"
    "'purge.started','purge.completed','purge.failed',"
    "'privileged_access.request','privileged_access.approve',"
    "'privileged_access.deny','privileged_access.activate',"
    "'privileged_access.notify_owner','privileged_access.use',"
    "'privileged_access.revoke','enterprise_identity.config_change',"
    "'enterprise_identity.scim_sync'"
)
_ELIGIBILITY_ACTIONS = (
    "'privileged_access.eligibility_change',"
    "'privileged_access.eligibility_review'"
)


def upgrade() -> None:
    Base.metadata.tables["platform_admin_eligibilities"].create(
        op.get_bind(),
        checkfirst=True,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "platform_admin_eligibilities TO vibecanvas_app"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_106},{_ELIGIBILITY_ACTIONS}))"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM audit_log WHERE action IN "
        f"({_ELIGIBILITY_ACTIONS})"
    )
    op.execute("DROP TABLE IF EXISTS platform_admin_eligibilities")
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_106}))"
    )
