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
        "user_webauthn_credentials",
        "user_webauthn_challenges",
    ):
        Base.metadata.tables[table].create(bind, checkfirst=True)
    # These retired tables are spelled out here rather than imported from the
    # current ORM metadata so a fresh install can replay history before the
    # later removal migration.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_mfa_totp (
            user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            secret_ciphertext TEXT NOT NULL,
            secret_nonce TEXT NOT NULL,
            secret_key_id UUID NOT NULL REFERENCES content_encryption_keys(key_id)
                ON DELETE RESTRICT,
            last_used_step BIGINT,
            recovery_code_hashes TEXT[] NOT NULL DEFAULT '{}',
            pending_expires_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_user_mfa_totp_status
                CHECK (status IN ('pending','active','disabled'))
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_mfa_totp_tenant "
        "ON user_mfa_totp(tenant_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_login_mfa_challenges (
            token_hash TEXT PRIMARY KEY,
            user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            audience TEXT NOT NULL DEFAULT 'web',
            available_methods TEXT[] NOT NULL DEFAULT '{}',
            webauthn_challenge BYTEA,
            failed_attempts INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_user_login_mfa_challenges_audience
                CHECK (audience IN ('web','extension','api')),
            CONSTRAINT ck_user_login_mfa_challenges_attempts
                CHECK (failed_attempts >= 0 AND failed_attempts <= 5)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_login_mfa_challenges_user "
        "ON user_login_mfa_challenges(user_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_user_login_mfa_challenges_expires "
        "ON user_login_mfa_challenges(expires_at)"
    )
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
