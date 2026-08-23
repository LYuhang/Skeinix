"""Remove initial-release authenticator and login MFA state.

Revision ID: 126
Revises: 125
Create Date: 2026-08-23
"""

from alembic import op


revision = "126"
down_revision = "125"
branch_labels = None
depends_on = None


_CURRENT_AUDIT_ACTIONS = (
    "'auth.login_success','auth.login_failure','auth.logout','auth.register',"
    "'auth.password_reset_request','auth.password_reset_complete',"
    "'auth.session_list','auth.session_revoke','auth.session_rotate',"
    "'auth.account_delete_request','auth.account_delete_cancel',"
    "'auth.passkey_register','auth.passkey_verify','auth.passkey_remove',"
    "'auth.sso_login_success','auth.sso_login_failure',"
    "'deployment.key_rotate','mcp_server.credential_change',"
    "'llm_credential.connection_change',"
    "'deployment.create','deployment.delete','mcp_server.create',"
    "'mcp_server.delete','workflow.delete','kb.delete',"
    "'organization.create','organization.update','organization.member_change',"
    "'share.lookup','share.grant','share.revoke','service_account.create',"
    "'service_account.status_change','secret.create','secret.destroy',"
    "'purge.started','purge.completed','purge.failed',"
    "'privileged_access.request','privileged_access.approve',"
    "'privileged_access.deny','privileged_access.activate',"
    "'privileged_access.notify_owner','privileged_access.use',"
    "'privileged_access.revoke','privileged_access.eligibility_change',"
    "'privileged_access.eligibility_review',"
    "'enterprise_identity.config_change','enterprise_identity.scim_sync'"
)

# Historical audit rows remain immutable and queryable after the feature is
# removed. Application code accepts only _CURRENT_AUDIT_ACTIONS for new rows.
_LEGACY_MFA_AUDIT_ACTIONS = (
    "'auth.mfa_enroll','auth.mfa_challenge',"
    "'auth.mfa_recovery','auth.mfa_disable'"
)


def _replace_audit_constraint(actions: str) -> None:
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action "
        f"CHECK (action IN ({actions}))"
    )


def upgrade() -> None:
    # Factor-authenticated sessions cannot be silently downgraded to password
    # authentication. Revoke them so users establish a fresh Session instead.
    op.execute(
        "DELETE FROM sessions "
        "WHERE authentication_strength IN ('totp','recovery')"
    )
    op.execute("DROP TABLE IF EXISTS user_login_mfa_challenges")
    op.execute("DROP TABLE IF EXISTS user_mfa_totp")
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "ck_sessions_authentication_strength"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT "
        "ck_sessions_authentication_strength CHECK "
        "(authentication_strength IN ('password','oauth','webauthn'))"
    )
    _replace_audit_constraint(
        f"{_CURRENT_AUDIT_ACTIONS},{_LEGACY_MFA_AUDIT_ACTIONS}"
    )


def downgrade() -> None:
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
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_mfa_totp, "
        "user_login_mfa_challenges TO vibecanvas_app"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "ck_sessions_authentication_strength"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT "
        "ck_sessions_authentication_strength CHECK "
        "(authentication_strength IN "
        "('password','oauth','totp','webauthn','recovery'))"
    )
    legacy_current_actions = _CURRENT_AUDIT_ACTIONS.replace(
        "'auth.passkey_register','auth.passkey_verify','auth.passkey_remove',",
        "",
    )
    _replace_audit_constraint(
        f"{legacy_current_actions},{_LEGACY_MFA_AUDIT_ACTIONS}"
    )
