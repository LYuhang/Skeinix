"""account deletion modes and audited hard-erasure boundary.

Revision ID: 120
Revises: 119
Create Date: 2026-08-10
"""
from alembic import op


revision = "120"
down_revision = "119"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "ADD COLUMN IF NOT EXISTS deletion_mode text NOT NULL "
        "DEFAULT 'immediate'"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "ADD COLUMN IF NOT EXISTS frozen_resource_state jsonb NOT NULL "
        "DEFAULT '{}'::jsonb"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "DROP CONSTRAINT IF EXISTS ck_account_deletion_requests_mode"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "ADD CONSTRAINT ck_account_deletion_requests_mode "
        "CHECK (deletion_mode IN ('immediate','delayed'))"
    )

    # The authorization ledger remains immutable during normal operation.
    # Erasure may replace only the deleted user reference with a fixed,
    # non-identifying marker; the resource, relation, revision, and mutation
    # semantics remain immutable.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_authz_mutation_payload()
        RETURNS trigger AS $$
        BEGIN
            IF current_setting('app.account_erasure', true) = 'on'
               AND ROW(
                    NEW.mutation_id, NEW.tenant_id, NEW.kind,
                    NEW.operation, NEW.desired_state,
                    NEW.object_type, NEW.object_id, NEW.relation,
                    NEW.subject_type, NEW.subject_relation,
                    NEW.source_revision, NEW.edge_revision,
                    NEW.supersedes_mutation_id, NEW.idempotency_key,
                    NEW.requested_at
               ) IS NOT DISTINCT FROM ROW(
                    OLD.mutation_id, OLD.tenant_id, OLD.kind,
                    OLD.operation, OLD.desired_state,
                    OLD.object_type, OLD.object_id, OLD.relation,
                    OLD.subject_type, OLD.subject_relation,
                    OLD.source_revision, OLD.edge_revision,
                    OLD.supersedes_mutation_id, OLD.idempotency_key,
                    OLD.requested_at
               )
               AND (
                    ROW(NEW.actor_type, NEW.actor_id)
                      IS NOT DISTINCT FROM ROW(OLD.actor_type, OLD.actor_id)
                    OR ROW(NEW.actor_type, NEW.actor_id)
                      IS NOT DISTINCT FROM ROW('system'::text, 'account-erasure'::text)
               )
               AND (
                    NEW.subject_id IS NOT DISTINCT FROM OLD.subject_id
                    OR (
                        OLD.subject_type = 'user'
                        AND NEW.subject_id = 'account-erasure'
                    )
               )
            THEN
                RETURN NEW;
            END IF;
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

    # Audit rows remain append-only for ordinary application and maintenance
    # work. Account erasure gets one narrowly constrained exception: the
    # maintenance-owned function below can remove every correlating/private
    # field while retaining only a non-identifying action/outcome timestamp.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
          IF TG_OP = 'UPDATE'
             AND current_setting('app.account_erasure', true) = 'on'
             AND EXISTS (
               SELECT 1 FROM pg_roles
                WHERE rolname = current_user
                  AND (rolsuper OR rolname = 'vibecanvas_maintenance')
             )
             AND NEW.audit_id = OLD.audit_id
             AND NEW.action = OLD.action
             AND NEW.outcome = OLD.outcome
             AND NEW.created_at = OLD.created_at
             AND NEW.tenant_id IS NULL
             AND NEW.actor_user_id IS NULL
             AND NEW.actor_email IS NULL
             AND NEW.target_id IS NULL
             AND NEW.target_name IS NULL
             AND NEW.ip_address IS NULL
             AND NEW.user_agent IS NULL
             AND NEW.request_id IS NULL
             AND NEW.meta = '{}'::jsonb
             AND NEW.actor_lookup_hash IS NULL
             AND NEW.ip_lookup_hash IS NULL
             AND NEW.private_ciphertext IS NULL
             AND NEW.private_nonce IS NULL
             AND NEW.private_key_id IS NULL
          THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'audit_log is append-only (no UPDATE/DELETE)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    # UPDATE must remain granted to the table owner because PostgreSQL checks
    # it while executing the users/audit_log ON DELETE SET NULL action, even
    # when the maintenance scrub left no matching row. This policy lets normal
    # application statements reach the append-only trigger for their own
    # tenant, while the trigger above remains the mutation boundary. The
    # erasure branch is additionally restricted to the maintenance role (or a
    # database superuser used by migration/integration tooling), so a client
    # cannot unlock it by setting the custom GUC itself.
    op.execute("DROP POLICY IF EXISTS audit_update_guard ON audit_log")
    op.execute(
        """
        CREATE POLICY audit_update_guard ON audit_log FOR UPDATE
        USING (
          tenant_id = current_setting('app.tenant_id', true)::uuid
          OR (
            current_setting('app.account_erasure', true) = 'on'
            AND EXISTS (
              SELECT 1 FROM pg_roles
               WHERE rolname = current_user
                 AND (rolsuper OR rolname = 'vibecanvas_maintenance')
            )
          )
        )
        WITH CHECK (
          tenant_id = current_setting('app.tenant_id', true)::uuid
          OR (
            current_setting('app.account_erasure', true) = 'on'
            AND EXISTS (
              SELECT 1 FROM pg_roles
               WHERE rolname = current_user
                 AND (rolsuper OR rolname = 'vibecanvas_maintenance')
            )
          )
        )
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION erase_account_audit(
          erased_user_id uuid,
          erased_tenant_id uuid
        ) RETURNS bigint
        LANGUAGE plpgsql
        SECURITY INVOKER
        SET search_path = pg_catalog, public
        AS $$
        DECLARE affected bigint;
        BEGIN
          PERFORM set_config('app.account_erasure', 'on', true);
          UPDATE public.audit_log
             SET tenant_id = NULL,
                 actor_user_id = NULL,
                 actor_email = NULL,
                 actor_lookup_hash = NULL,
                 target_id = NULL,
                 target_name = NULL,
                 ip_address = NULL,
                 ip_lookup_hash = NULL,
                 user_agent = NULL,
                 request_id = NULL,
                 meta = '{}'::jsonb,
                 private_ciphertext = NULL,
                 private_nonce = NULL,
                 private_key_id = NULL
           WHERE tenant_id = erased_tenant_id
              OR actor_user_id = erased_user_id;
          GET DIAGNOSTICS affected = ROW_COUNT;
          RETURN affected;
        END;
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION erase_account_audit(uuid, uuid) FROM PUBLIC")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vibecanvas_maintenance') THEN
            GRANT UPDATE ON audit_log TO vibecanvas_maintenance;
            GRANT EXECUTE ON FUNCTION erase_account_audit(uuid, uuid)
              TO vibecanvas_maintenance;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vibecanvas_app') THEN
            -- Required by the users/audit_log referential action. RLS and the
            -- append-only trigger above still reject every ordinary mutation.
            GRANT UPDATE ON audit_log TO vibecanvas_app;
            REVOKE DELETE ON audit_log FROM vibecanvas_app;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS erase_account_audit(uuid, uuid)")
    op.execute("DROP POLICY IF EXISTS audit_update_guard ON audit_log")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only (no UPDATE/DELETE)';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION protect_authz_mutation_payload()
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
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vibecanvas_maintenance') THEN
            REVOKE UPDATE ON audit_log FROM vibecanvas_maintenance;
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vibecanvas_app') THEN
            REVOKE UPDATE ON audit_log FROM vibecanvas_app;
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "DROP CONSTRAINT IF EXISTS ck_account_deletion_requests_mode"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests DROP COLUMN IF EXISTS deletion_mode"
    )
    op.execute(
        "ALTER TABLE account_deletion_requests "
        "DROP COLUMN IF EXISTS frozen_resource_state"
    )
