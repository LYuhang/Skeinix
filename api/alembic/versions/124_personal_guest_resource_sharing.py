"""Add recipient-scoped resource sharing projection and email lookup.

Revision ID: 124
Revises: 123
Create Date: 2026-08-23
"""

from alembic import op

from vibecanvas_api.storage.models import Base


revision = "124"
down_revision = "123"
branch_labels = None
depends_on = None


_AUDIT_ACTIONS = (
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
_AUDIT_ACTIONS_WITHOUT_LOOKUP = _AUDIT_ACTIONS.replace("'share.lookup',", "")


def upgrade() -> None:
    # Fresh installs use current metadata in revision 001, so every DDL step is
    # idempotent when the column/table already exists.
    op.execute(
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
        "profile_email_lookup_hash TEXT"
    )
    # Password identities already use the same keyed, case-insensitive digest
    # domain. This backfills ordinary existing accounts without decrypting PII.
    op.execute(
        """
        UPDATE users AS u
           SET profile_email_lookup_hash = i.provider_uid_lookup_hash
          FROM auth_identities AS i
         WHERE i.user_id = u.user_id
           AND i.provider = 'password'
           AND u.profile_email_lookup_hash IS NULL
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_users_profile_email_lookup "
        "ON users(profile_email_lookup_hash)"
    )

    Base.metadata.tables["shared_resource_projections"].create(
        op.get_bind(),
        checkfirst=True,
    )
    op.execute(
        "ALTER TABLE shared_resource_projections ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE shared_resource_projections FORCE ROW LEVEL SECURITY"
    )
    for policy in (
        "shared_resource_projection_select",
        "shared_resource_projection_insert",
        "shared_resource_projection_update",
        "shared_resource_projection_delete",
    ):
        op.execute(
            f"DROP POLICY IF EXISTS {policy} "
            "ON shared_resource_projections"
        )
    op.execute(
        """
        CREATE POLICY shared_resource_projection_select
        ON shared_resource_projections FOR SELECT
        USING (
            owner_tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
            OR recipient_user_id = NULLIF(
                current_setting('app.user_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY shared_resource_projection_insert
        ON shared_resource_projections FOR INSERT
        WITH CHECK (
            owner_tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY shared_resource_projection_update
        ON shared_resource_projections FOR UPDATE
        USING (
            owner_tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        WITH CHECK (
            owner_tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY shared_resource_projection_delete
        ON shared_resource_projections FOR DELETE
        USING (
            owner_tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON "
        "shared_resource_projections TO vibecanvas_app"
    )

    # Preserve already-applied direct User grants for the four product roots.
    op.execute(
        """
        INSERT INTO shared_resource_projections (
            owner_tenant_id, resource_type, resource_id,
            recipient_user_id, relation, source_mutation_id,
            edge_revision, granted_at, updated_at
        )
        SELECT m.tenant_id, m.object_type, m.object_id,
               m.subject_id::uuid, m.relation, m.mutation_id,
               m.edge_revision, COALESCE(m.applied_at, m.requested_at), now()
          FROM authz_mutations AS m
          JOIN authz_edge_revisions AS e
            ON e.tenant_id = m.tenant_id
           AND e.object_type = m.object_type
           AND e.object_id = m.object_id
           AND e.relation = m.relation
           AND e.subject_type = m.subject_type
           AND e.subject_id = m.subject_id
           AND e.subject_relation = COALESCE(m.subject_relation, '')
           AND e.current_revision = m.edge_revision
         WHERE m.kind = 'direct_binding'
           AND m.desired_state = 'present'
           AND m.status = 'applied'
           AND m.subject_type = 'user'
           AND m.object_type IN (
               'workflow','task','deployment','knowledge_base'
           )
        ON CONFLICT DO NOTHING
        """
    )

    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_AUDIT_ACTIONS}))"
    )


def downgrade() -> None:
    op.execute("DELETE FROM audit_log WHERE action='share.lookup'")
    op.execute("ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action")
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_action CHECK "
        f"(action IN ({_AUDIT_ACTIONS_WITHOUT_LOOKUP}))"
    )
    op.execute("DROP TABLE IF EXISTS shared_resource_projections")
    op.execute("DROP INDEX IF EXISTS ix_users_profile_email_lookup")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS profile_email_lookup_hash")
