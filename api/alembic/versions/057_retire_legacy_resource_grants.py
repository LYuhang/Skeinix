"""Move legacy direct grants into the durable authorization ledger.

Revision ID: 057
Revises: 056
Create Date: 2026-07-31

The old ``resource_grants`` table is no longer a live authorization source.
Every supported non-public row becomes a requested, revisioned
``direct_binding`` intent before the table is removed. Public wildcard grants
are deliberately not carried forward; public publishing uses immutable
publication artifacts instead of access to mutable business rows.
"""

from alembic import op


revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # All three tables are FORCE-RLS protected. Alembic runs as their owner,
    # so temporarily restore owner bypass for this cross-tenant migration.
    for table in (
        "resource_grants",
        "authz_edge_revisions",
        "authz_mutations",
    ):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        DECLARE
            grant_row record;
            allocated_revision bigint;
        BEGIN
            FOR grant_row IN
                SELECT
                    grant_id,
                    tenant_id,
                    resource_type,
                    resource_id,
                    principal_type,
                    principal_id,
                    level
                FROM resource_grants
                WHERE principal_type <> 'public'
                  AND principal_id IS NOT NULL
                  AND resource_type IN (
                      'workflow', 'template', 'task', 'deployment'
                  )
                  AND principal_type IN (
                      'user', 'group', 'organization'
                  )
                  AND level IN ('viewer', 'editor', 'manager')
                ORDER BY tenant_id, grant_id
            LOOP
                -- A ledger entry created after migration 056 is newer than
                -- the legacy row and wins, including an explicit revoke.
                IF EXISTS (
                    SELECT 1
                    FROM authz_mutations AS mutation
                    WHERE mutation.tenant_id = grant_row.tenant_id
                      AND mutation.object_type = grant_row.resource_type
                      AND mutation.object_id = grant_row.resource_id
                      AND mutation.relation = grant_row.level
                      AND mutation.subject_type = grant_row.principal_type
                      AND mutation.subject_id =
                          grant_row.principal_id::text
                      AND COALESCE(mutation.subject_relation, '') =
                          CASE
                              WHEN grant_row.principal_type IN (
                                  'group', 'organization'
                              ) THEN 'member'
                              ELSE ''
                          END
                ) THEN
                    CONTINUE;
                END IF;

                INSERT INTO authz_edge_revisions (
                    tenant_id,
                    object_type,
                    object_id,
                    relation,
                    subject_type,
                    subject_id,
                    subject_relation,
                    current_revision
                ) VALUES (
                    grant_row.tenant_id,
                    grant_row.resource_type,
                    grant_row.resource_id,
                    grant_row.level,
                    grant_row.principal_type,
                    grant_row.principal_id::text,
                    CASE
                        WHEN grant_row.principal_type IN (
                            'group', 'organization'
                        ) THEN 'member'
                        ELSE ''
                    END,
                    1
                )
                ON CONFLICT (
                    tenant_id,
                    object_type,
                    object_id,
                    relation,
                    subject_type,
                    subject_id,
                    subject_relation
                ) DO UPDATE SET
                    current_revision =
                        authz_edge_revisions.current_revision + 1,
                    updated_at = now()
                RETURNING current_revision INTO allocated_revision;

                INSERT INTO authz_mutations (
                    mutation_id,
                    tenant_id,
                    actor_type,
                    actor_id,
                    kind,
                    operation,
                    desired_state,
                    object_type,
                    object_id,
                    relation,
                    subject_type,
                    subject_id,
                    subject_relation,
                    source_revision,
                    edge_revision,
                    supersedes_mutation_id,
                    status,
                    revocation_guard_active,
                    idempotency_key
                ) VALUES (
                    gen_random_uuid(),
                    grant_row.tenant_id,
                    'system',
                    'migration-057',
                    'direct_binding',
                    'write',
                    'present',
                    grant_row.resource_type,
                    grant_row.resource_id,
                    grant_row.level,
                    grant_row.principal_type,
                    grant_row.principal_id::text,
                    CASE
                        WHEN grant_row.principal_type IN (
                            'group', 'organization'
                        ) THEN 'member'
                        ELSE NULL
                    END,
                    'legacy-resource-grant:' || grant_row.grant_id::text,
                    allocated_revision,
                    NULL,
                    'requested',
                    false,
                    'legacy-resource-grant:' || grant_row.grant_id::text
                );
            END LOOP;
        END
        $$;
        """
    )

    op.execute("DROP TABLE resource_grants")
    for table in ("authz_edge_revisions", "authz_mutations"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    raise RuntimeError(
        "revision 057 is intentionally irreversible: the retired permission "
        "framework cannot be restored"
    )
