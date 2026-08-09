"""Parallel ciphertext storage for Chat messages and Workflow versions.

Revision ID: 067
Revises: 066
Create Date: 2026-07-31
"""
from alembic import op


revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS content_encryption_keys (
            key_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            resource_type text NOT NULL,
            resource_id text NOT NULL,
            version integer NOT NULL DEFAULT 1,
            status text NOT NULL DEFAULT 'active',
            algorithm text NOT NULL DEFAULT 'AES-256-GCM',
            wrapped_dek text NOT NULL,
            wrapping_key_id text NOT NULL,
            wrapping_key_version text NOT NULL,
            context_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            rotated_at timestamptz,
            CONSTRAINT ck_content_keys_version CHECK (version > 0),
            CONSTRAINT ck_content_keys_status CHECK (
              status IN ('active','retired','destroyed')
            ),
            CONSTRAINT ck_content_keys_algorithm CHECK (
              algorithm = 'AES-256-GCM'
            ),
            CONSTRAINT uq_content_keys_resource_version UNIQUE (
              tenant_id, resource_type, resource_id, version
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_content_keys_resource "
        "ON content_encryption_keys "
        "(tenant_id, resource_type, resource_id, status)"
    )
    op.execute("ALTER TABLE content_encryption_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE content_encryption_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS content_keys_tenant_isolation "
        "ON content_encryption_keys"
    )
    op.execute(
        "CREATE POLICY content_keys_tenant_isolation ON content_encryption_keys "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON content_encryption_keys TO vibecanvas_app")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS content_ciphertext text")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS content_nonce text")
    op.execute("ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS content_key_id uuid")
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
        "AND tc.table_schema = kcu.table_schema WHERE tc.table_schema = current_schema() "
        "AND tc.table_name = 'chat_messages' AND tc.constraint_type = 'FOREIGN KEY' "
        "AND kcu.column_name = 'content_key_id') THEN ALTER TABLE chat_messages ADD "
        "CONSTRAINT fk_chat_messages_content_key FOREIGN KEY (content_key_id) "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT; END IF; END $$"
    )
    op.execute("ALTER TABLE workflow_versions ADD COLUMN IF NOT EXISTS workflow_ciphertext text")
    op.execute("ALTER TABLE workflow_versions ADD COLUMN IF NOT EXISTS workflow_nonce text")
    op.execute("ALTER TABLE workflow_versions ADD COLUMN IF NOT EXISTS workflow_key_id uuid")
    op.execute(
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
        "AND tc.table_schema = kcu.table_schema WHERE tc.table_schema = current_schema() "
        "AND tc.table_name = 'workflow_versions' AND tc.constraint_type = 'FOREIGN KEY' "
        "AND kcu.column_name = 'workflow_key_id') THEN ALTER TABLE workflow_versions ADD "
        "CONSTRAINT fk_workflow_versions_content_key FOREIGN KEY (workflow_key_id) "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT; END IF; END $$"
    )


def downgrade() -> None:
    # Refuse to silently discard the only copy of encrypted content.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM chat_messages WHERE content_key_id IS NOT NULL)
             OR EXISTS (SELECT 1 FROM workflow_versions WHERE workflow_key_id IS NOT NULL)
          THEN RAISE EXCEPTION 'decrypt content before downgrading revision 067';
          END IF;
        END $$
        """
    )
    op.execute("ALTER TABLE workflow_versions DROP COLUMN workflow_key_id")
    op.execute("ALTER TABLE workflow_versions DROP COLUMN workflow_nonce")
    op.execute("ALTER TABLE workflow_versions DROP COLUMN workflow_ciphertext")
    op.execute("ALTER TABLE chat_messages DROP COLUMN content_key_id")
    op.execute("ALTER TABLE chat_messages DROP COLUMN content_nonce")
    op.execute("ALTER TABLE chat_messages DROP COLUMN content_ciphertext")
    op.execute("DROP TABLE content_encryption_keys")
