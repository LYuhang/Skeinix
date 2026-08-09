"""Durable OpenFGA mutation intent, edge revisions, and revocation guards.

Revision ID: 056
Revises: 055
Create Date: 2026-07-31
"""

from alembic import op


revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS authz_edge_revisions (
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            object_type text NOT NULL,
            object_id text NOT NULL,
            relation text NOT NULL,
            subject_type text NOT NULL,
            subject_id text NOT NULL,
            subject_relation text NOT NULL DEFAULT '',
            current_revision bigint NOT NULL CHECK (current_revision > 0),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (
                tenant_id, object_type, object_id, relation,
                subject_type, subject_id, subject_relation
            ),
            CONSTRAINT ck_authz_edge_object_type CHECK (
                object_type IN (
                    'organization','group','chat','workflow','template',
                    'task','deployment','storage_root','knowledge_base',
                    'mcp_installation','skill_installation',
                    'llm_credential','service_account'
                )
            ),
            CONSTRAINT ck_authz_edge_subject_type CHECK (
                subject_type IN (
                    'user','service_account','group','organization'
                )
            )
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS authz_mutations (
            mutation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            actor_type text NOT NULL,
            actor_id text NOT NULL,
            kind text NOT NULL,
            operation text NOT NULL,
            desired_state text NOT NULL,
            object_type text NOT NULL,
            object_id text NOT NULL,
            relation text NOT NULL,
            subject_type text NOT NULL,
            subject_id text NOT NULL,
            subject_relation text,
            source_revision text,
            edge_revision bigint NOT NULL CHECK (edge_revision > 0),
            supersedes_mutation_id uuid
                REFERENCES authz_mutations(mutation_id) ON DELETE SET NULL,
            status text NOT NULL DEFAULT 'requested',
            revocation_guard_active boolean NOT NULL DEFAULT false,
            idempotency_key text NOT NULL,
            error_code text,
            attempt_count integer NOT NULL DEFAULT 0
                CHECK (attempt_count >= 0),
            next_attempt_at timestamptz,
            requested_at timestamptz NOT NULL DEFAULT now(),
            applied_at timestamptz,
            CONSTRAINT uq_authz_mutation_idempotency
                UNIQUE(tenant_id, idempotency_key),
            CONSTRAINT ck_authz_mutation_actor_type CHECK (
                actor_type IN ('user','service_account','system')
            ),
            CONSTRAINT ck_authz_mutation_kind CHECK (
                kind IN ('structural_projection','direct_binding')
            ),
            CONSTRAINT ck_authz_mutation_operation CHECK (
                operation IN ('write','delete')
            ),
            CONSTRAINT ck_authz_mutation_desired_state CHECK (
                desired_state IN ('present','absent')
            ),
            CONSTRAINT ck_authz_mutation_operation_matches_desired CHECK (
                (operation = 'write' AND desired_state = 'present')
                OR
                (operation = 'delete' AND desired_state = 'absent')
            ),
            CONSTRAINT ck_authz_mutation_object_type CHECK (
                object_type IN (
                    'organization','group','chat','workflow','template',
                    'task','deployment','storage_root','knowledge_base',
                    'mcp_installation','skill_installation',
                    'llm_credential','service_account'
                )
            ),
            CONSTRAINT ck_authz_mutation_subject_type CHECK (
                subject_type IN (
                    'user','service_account','group','organization'
                )
            ),
            CONSTRAINT ck_authz_mutation_status CHECK (
                status IN ('requested','applied','failed','superseded')
            ),
            CONSTRAINT ck_authz_mutation_applied_at CHECK (
                (status = 'applied' AND applied_at IS NOT NULL)
                OR
                (status <> 'applied')
            ),
            CONSTRAINT ck_authz_mutation_revocation_guard CHECK (
                NOT revocation_guard_active
                OR (
                    desired_state = 'absent'
                    AND status IN ('requested','failed')
                )
            )
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_authz_mutation_edge_revision
        ON authz_mutations (
            tenant_id, object_type, object_id, relation,
            subject_type, subject_id, COALESCE(subject_relation, ''),
            edge_revision
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_authz_mutations_reconcile
        ON authz_mutations (
            status, COALESCE(next_attempt_at, requested_at), requested_at
        )
        WHERE status IN ('requested','failed')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_authz_mutations_revocation_guard
        ON authz_mutations (
            tenant_id, object_type, object_id, relation,
            subject_type, subject_id
        )
        WHERE revocation_guard_active
        """
    )

    # Mutation payload and revision are immutable after durable intent commit.
    # Only reconciliation state may advance.
    op.execute(
        """
        CREATE FUNCTION protect_authz_mutation_payload()
        RETURNS trigger AS $$
        BEGIN
            IF ROW(
                NEW.mutation_id, NEW.tenant_id, NEW.actor_type, NEW.actor_id,
                NEW.kind, NEW.operation, NEW.desired_state,
                NEW.object_type, NEW.object_id, NEW.relation,
                NEW.subject_type, NEW.subject_id, NEW.subject_relation,
                NEW.source_revision, NEW.edge_revision,
                NEW.supersedes_mutation_id, NEW.idempotency_key,
                NEW.requested_at
            ) IS DISTINCT FROM ROW(
                OLD.mutation_id, OLD.tenant_id, OLD.actor_type, OLD.actor_id,
                OLD.kind, OLD.operation, OLD.desired_state,
                OLD.object_type, OLD.object_id, OLD.relation,
                OLD.subject_type, OLD.subject_id, OLD.subject_relation,
                OLD.source_revision, OLD.edge_revision,
                OLD.supersedes_mutation_id, OLD.idempotency_key,
                OLD.requested_at
            ) THEN
                RAISE EXCEPTION 'authz mutation payload is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_authz_mutation_payload_immutable
        BEFORE UPDATE ON authz_mutations
        FOR EACH ROW EXECUTE FUNCTION protect_authz_mutation_payload()
        """
    )

    for table in ("authz_edge_revisions", "authz_mutations"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (
                tenant_id = NULLIF(
                    current_setting('app.tenant_id', true), ''
                )::uuid
            )
            WITH CHECK (
                tenant_id = NULLIF(
                    current_setting('app.tenant_id', true), ''
                )::uuid
            )
            """
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_authz_mutation_payload_immutable "
        "ON authz_mutations"
    )
    op.execute("DROP FUNCTION IF EXISTS protect_authz_mutation_payload()")
    op.execute("DROP TABLE IF EXISTS authz_mutations")
    op.execute("DROP TABLE IF EXISTS authz_edge_revisions")
