"""Add TOTP/WebAuthn factors and expiring Session step-up.

Revision ID: 103
Revises: 102
Create Date: 2026-08-01
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "103"
down_revision = "102"
branch_labels = None
depends_on = None

_ACTIONS = (
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
    for table in (
        "user_mfa_totp",
        "user_webauthn_credentials",
        "user_webauthn_challenges",
        "user_login_mfa_challenges",
    ):
        Base.metadata.tables[table].create(bind, checkfirst=True)
    op.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS "
        "step_up_expires_at TIMESTAMPTZ"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_mfa_totp, "
        "user_webauthn_credentials, user_webauthn_challenges, "
        "user_login_mfa_challenges TO vibecanvas_app"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "ck_sessions_authentication_strength"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT "
        "ck_sessions_authentication_strength CHECK (authentication_strength IN "
        "('password','oauth','totp','webauthn','recovery'))"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        f"ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action "
        f"CHECK (action IN ({_ACTIONS}))"
    )


def downgrade() -> None:
    old_actions = _ACTIONS.replace(
        "'auth.mfa_enroll','auth.mfa_challenge','auth.mfa_recovery',"
        "'auth.mfa_disable',",
        "",
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        f"ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action "
        f"CHECK (action IN ({old_actions}))"
    )
    op.execute(
        "UPDATE sessions SET authentication_strength = 'mfa' "
        "WHERE authentication_strength IN ('totp','webauthn')"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "ck_sessions_authentication_strength"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT "
        "ck_sessions_authentication_strength CHECK (authentication_strength IN "
        "('password','oauth','mfa','recovery'))"
    )
    op.execute("DROP TABLE IF EXISTS user_webauthn_challenges")
    op.execute("DROP TABLE IF EXISTS user_login_mfa_challenges")
    op.execute("DROP TABLE IF EXISTS user_webauthn_credentials")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS step_up_expires_at")
    op.execute("DROP TABLE IF EXISTS user_mfa_totp")
