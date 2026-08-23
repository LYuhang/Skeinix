"""Add OpenRouter PKCE connections and dynamic model catalogs.

Revision ID: 125
Revises: 124
Create Date: 2026-08-23
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "125"
down_revision = "124"
branch_labels = None
depends_on = None


_ACTION = "'llm_credential.connection_change'"
_PREVIOUS_ACTIONS = (
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


def upgrade() -> None:
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS "
        "connection_kind TEXT NOT NULL DEFAULT 'manual'"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS "
        "model_catalog JSONB NOT NULL DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS "
        "catalog_refreshed_at TIMESTAMPTZ"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS "
        "catalog_error_code TEXT"
    )
    op.execute(
        "ALTER TABLE llm_credentials DROP CONSTRAINT IF EXISTS "
        "ck_llm_credentials_connection_kind"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD CONSTRAINT "
        "ck_llm_credentials_connection_kind CHECK "
        "(connection_kind IN ('manual','openrouter_oauth'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_llm_openrouter_user_connection "
        "ON llm_credentials(tenant_id, user_id) "
        "WHERE deleted_at IS NULL AND connection_kind='openrouter_oauth'"
    )

    Base.metadata.tables["openrouter_oauth_states"].create(
        op.get_bind(), checkfirst=True,
    )
    op.execute("ALTER TABLE openrouter_oauth_states ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE openrouter_oauth_states FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS openrouter_oauth_states_owner "
        "ON openrouter_oauth_states"
    )
    op.execute(
        "CREATE POLICY openrouter_oauth_states_owner ON openrouter_oauth_states "
        "USING (tenant_id=NULLIF(current_setting('app.tenant_id', true),'')::uuid "
        "AND user_id=NULLIF(current_setting('app.user_id', true),'')::uuid) "
        "WITH CHECK (tenant_id=NULLIF(current_setting('app.tenant_id', true),'')::uuid "
        "AND user_id=NULLIF(current_setting('app.user_id', true),'')::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON openrouter_oauth_states "
        "TO vibecanvas_app"
    )

    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_PREVIOUS_ACTIONS},{_ACTION}))"
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM audit_log WHERE action={_ACTION}"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_PREVIOUS_ACTIONS}))"
    )
    op.execute("DROP TABLE IF EXISTS openrouter_oauth_states")
    op.execute("DROP INDEX IF EXISTS uq_llm_openrouter_user_connection")
    op.execute(
        "ALTER TABLE llm_credentials DROP CONSTRAINT IF EXISTS "
        "ck_llm_credentials_connection_kind"
    )
    for column in (
        "catalog_error_code", "catalog_refreshed_at", "model_catalog",
        "connection_kind",
    ):
        op.execute(f"ALTER TABLE llm_credentials DROP COLUMN IF EXISTS {column}")
