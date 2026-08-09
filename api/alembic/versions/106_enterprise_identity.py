"""Add enterprise OIDC, SCIM, and IdP-managed group state.

Revision ID: 106
Revises: 105
Create Date: 2026-08-02
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "106"
down_revision = "105"
branch_labels = None
depends_on = None

_ACTIONS_105 = (
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
    "'privileged_access.notify_owner','privileged_access.use',"
    "'privileged_access.revoke'"
)
_ENTERPRISE_ACTIONS = (
    "'auth.sso_login_success','auth.sso_login_failure',"
    "'enterprise_identity.config_change','enterprise_identity.scim_sync'"
)


def _add_org_columns() -> None:
    op.execute(
        "ALTER TABLE groups ADD COLUMN IF NOT EXISTS "
        "directory_provider_id UUID"
    )
    op.execute(
        "ALTER TABLE groups ADD COLUMN IF NOT EXISTS external_id TEXT"
    )
    op.execute(
        "ALTER TABLE groups ADD COLUMN IF NOT EXISTS "
        "external_id_lookup_hash TEXT"
    )
    op.execute(
        "ALTER TABLE org_memberships ADD COLUMN IF NOT EXISTS "
        "source TEXT NOT NULL DEFAULT 'native'"
    )
    op.execute(
        "ALTER TABLE org_memberships ADD COLUMN IF NOT EXISTS "
        "directory_provider_id UUID"
    )
    op.execute(
        "ALTER TABLE group_memberships ADD COLUMN IF NOT EXISTS "
        "source TEXT NOT NULL DEFAULT 'native'"
    )


def _rebuild_constraints() -> None:
    op.execute(
        "UPDATE groups SET source='native' "
        "WHERE source='idp' AND directory_provider_id IS NULL"
    )
    op.execute(
        "ALTER TABLE groups DROP CONSTRAINT IF EXISTS "
        "fk_groups_directory_provider"
    )
    op.execute(
        "ALTER TABLE groups ADD CONSTRAINT fk_groups_directory_provider "
        "FOREIGN KEY (directory_provider_id) REFERENCES "
        "enterprise_identity_providers(provider_id) ON DELETE CASCADE"
    )
    op.execute(
        "ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS "
        "fk_org_memberships_directory_provider"
    )
    op.execute(
        "ALTER TABLE org_memberships ADD CONSTRAINT "
        "fk_org_memberships_directory_provider FOREIGN KEY "
        "(directory_provider_id) REFERENCES "
        "enterprise_identity_providers(provider_id) ON DELETE CASCADE"
    )
    for table, constraint in (
        ("groups", "ck_groups_directory_source"),
        ("org_memberships", "ck_org_memberships_source"),
        ("org_memberships", "ck_org_memberships_directory_source"),
        ("group_memberships", "ck_group_membership_source"),
    ):
        op.execute(
            f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {constraint}"
        )
    op.execute(
        "ALTER TABLE groups ADD CONSTRAINT ck_groups_directory_source CHECK "
        "((source = 'native' AND directory_provider_id IS NULL "
        "AND external_id IS NULL AND external_id_lookup_hash IS NULL) OR "
        "(source = 'idp' AND directory_provider_id IS NOT NULL "
        "AND external_id_lookup_hash IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE org_memberships ADD CONSTRAINT "
        "ck_org_memberships_source CHECK (source IN ('native','scim'))"
    )
    op.execute(
        "ALTER TABLE org_memberships ADD CONSTRAINT "
        "ck_org_memberships_directory_source CHECK "
        "((source = 'native' AND directory_provider_id IS NULL) OR "
        "(source = 'scim' AND directory_provider_id IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE group_memberships ADD CONSTRAINT "
        "ck_group_membership_source CHECK (source IN ('native','idp'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_groups_directory_external_id ON groups "
        "(directory_provider_id, external_id_lookup_hash)"
    )


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.tables["enterprise_identity_providers"].create(
        bind, checkfirst=True,
    )
    op.execute(
        "ALTER TABLE enterprise_identity_providers ADD COLUMN IF NOT EXISTS "
        "organization_slug TEXT"
    )
    op.execute(
        "UPDATE enterprise_identity_providers p SET organization_slug=o.slug "
        "FROM organizations o WHERE p.tenant_id=o.tenant_id "
        "AND p.organization_slug IS NULL"
    )
    op.execute(
        "ALTER TABLE enterprise_identity_providers ALTER COLUMN "
        "organization_slug SET NOT NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS "
        "ix_enterprise_identity_provider_organization_slug ON "
        "enterprise_identity_providers (organization_slug, status)"
    )
    _add_org_columns()
    _rebuild_constraints()
    Base.metadata.tables["enterprise_directory_users"].create(
        bind, checkfirst=True,
    )
    Base.metadata.tables["oidc_login_transactions"].create(
        bind, checkfirst=True,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "enterprise_identity_providers, enterprise_directory_users, "
        "oidc_login_transactions TO vibecanvas_app"
    )
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_105},{_ENTERPRISE_ACTIONS}))"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM audit_log WHERE action IN "
        f"({_ENTERPRISE_ACTIONS})"
    )
    op.execute("DROP TABLE IF EXISTS oidc_login_transactions")
    op.execute("DROP TABLE IF EXISTS enterprise_directory_users")
    op.execute(
        "ALTER TABLE groups DROP CONSTRAINT IF EXISTS "
        "ck_groups_directory_source"
    )
    op.execute(
        "DROP INDEX IF EXISTS uq_groups_directory_external_id"
    )
    op.execute(
        "ALTER TABLE groups DROP CONSTRAINT IF EXISTS "
        "fk_groups_directory_provider"
    )
    op.execute(
        "ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS "
        "ck_org_memberships_directory_source"
    )
    op.execute(
        "ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS "
        "ck_org_memberships_source"
    )
    op.execute(
        "ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS "
        "fk_org_memberships_directory_provider"
    )
    op.execute(
        "ALTER TABLE group_memberships DROP CONSTRAINT IF EXISTS "
        "ck_group_membership_source"
    )
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS external_id_lookup_hash")
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS external_id")
    op.execute("ALTER TABLE groups DROP COLUMN IF EXISTS directory_provider_id")
    op.execute("ALTER TABLE org_memberships DROP COLUMN IF EXISTS directory_provider_id")
    op.execute("ALTER TABLE org_memberships DROP COLUMN IF EXISTS source")
    op.execute("ALTER TABLE group_memberships DROP COLUMN IF EXISTS source")
    op.execute("DROP TABLE IF EXISTS enterprise_identity_providers")
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_ACTIONS_105}))"
    )
