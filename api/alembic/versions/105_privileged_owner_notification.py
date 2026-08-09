"""Add the customer-owner privileged-access notification audit action.

Revision ID: 105
Revises: 104
Create Date: 2026-08-02
"""

from alembic import op


revision = "105"
down_revision = "104"
branch_labels = None
depends_on = None

_ACTIONS_104 = (
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
    "'purge.started','purge.completed','purge.failed',"
    "'privileged_access.request','privileged_access.approve',"
    "'privileged_access.deny','privileged_access.activate',"
    "'privileged_access.use','privileged_access.revoke'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_104},'privileged_access.notify_owner'))"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM audit_log "
        "WHERE action = 'privileged_access.notify_owner'"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_104}))"
    )
