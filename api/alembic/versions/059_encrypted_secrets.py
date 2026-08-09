"""Envelope-encrypted SecretService records and LLM secret references.

Revision ID: 059
Revises: 058
Create Date: 2026-07-31
"""

from alembic import op


revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS encrypted_secrets (
            secret_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id)
                ON DELETE CASCADE,
            purpose text NOT NULL,
            resource_type text NOT NULL,
            resource_id text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            status text NOT NULL DEFAULT 'active',
            algorithm text NOT NULL DEFAULT 'AES-256-GCM',
            ciphertext text,
            nonce text,
            wrapped_dek text,
            wrapping_key_id text NOT NULL,
            wrapping_key_version text NOT NULL,
            context_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            destroyed_at timestamptz,
            CONSTRAINT ck_encrypted_secrets_version CHECK (version > 0),
            CONSTRAINT ck_encrypted_secrets_status CHECK (
                status IN ('active','superseded','destroyed')
            ),
            CONSTRAINT ck_encrypted_secrets_algorithm CHECK (
                algorithm = 'AES-256-GCM'
            ),
            CONSTRAINT uq_encrypted_secrets_resource_version UNIQUE (
                tenant_id, purpose, resource_type, resource_id, version
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_encrypted_secrets_resource "
        "ON encrypted_secrets "
        "(tenant_id, resource_type, resource_id, purpose)"
    )
    op.execute("ALTER TABLE encrypted_secrets ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE encrypted_secrets FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS encrypted_secrets_tenant_isolation "
        "ON encrypted_secrets"
    )
    op.execute(
        "CREATE POLICY encrypted_secrets_tenant_isolation "
        "ON encrypted_secrets USING ("
        "tenant_id = current_setting('app.tenant_id', true)::uuid"
        ") WITH CHECK ("
        "tenant_id = current_setting('app.tenant_id', true)::uuid"
        ")"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS secret_ref uuid "
        "REFERENCES encrypted_secrets(secret_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS secret_version integer "
        "NOT NULL DEFAULT 1"
    )
    # Existing rows are migrated by the host-side encrypt-legacy-secrets
    # command before the plaintext column is retired. New writes use only ref.
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='llm_credentials' "
        "AND column_name='api_key') THEN ALTER TABLE llm_credentials "
        "ALTER COLUMN api_key DROP NOT NULL; END IF; END $$"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 059 is intentionally irreversible: SecretService records "
        "cannot be downgraded to plaintext columns"
    )
