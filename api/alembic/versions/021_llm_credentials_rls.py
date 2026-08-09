"""llm_credentials — FORCE RLS + tenant policy + partial unique index.

Revision ID: 021
Revises: 020
Create Date: 2026-06-11

This migration CREATEs the ``llm_credentials`` table idempotently (CREATE TABLE
IF NOT EXISTS): on a fresh DB migration 001's ``Base.metadata.create_all(bind)``
already built it (``LlmCredential`` registers via ``storage/models.py``'s
tail-import), so the create is a no-op; on a DB initialised BEFORE this model
existed, 001 never made it, so this migration does. It then adds what
``create_all`` cannot express:

  - partial UNIQUE index on ``(tenant_id, name) WHERE deleted_at IS NULL``
  - a partial regular index on ``(tenant_id, enabled)``
  - row-level security (ENABLE + FORCE) + the ``tenant_isolation`` policy

Structure mirrors migration 006 (mcp_servers) byte-for-byte. The single
``FOR ALL`` policy covers SELECT / INSERT / UPDATE / DELETE (Phase 5 /
Deployments / MCP convention). GRANT is explicit to keep role-scope review
uniform.
"""
from alembic import op

revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade():
    # ----- Create the table -----
    # 001's create_all only builds tables that existed on Base.metadata WHEN 001
    # ran. A database initialised before this model was added (any pre-existing
    # deployment) never got the table, so create it idempotently here: CREATE
    # TABLE IF NOT EXISTS is a no-op on a fresh DB (create_all already made it)
    # and creates it on a pre-existing DB. Columns mirror models_llm_credentials.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS llm_credentials (
            id          uuid PRIMARY KEY,
            tenant_id   uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            user_id     uuid NOT NULL REFERENCES users(user_id),
            name        text NOT NULL,
            description text,
            provider    text NOT NULL,
            model_name  text NOT NULL,
            api_url     text,
            api_key     text NOT NULL,
            enabled     boolean NOT NULL DEFAULT true,
            deleted_at  timestamptz,
            created_at  timestamptz NOT NULL DEFAULT now(),
            updated_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # ----- Partial indexes on llm_credentials -----
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_llm_credentials_tenant_name "
        "ON llm_credentials (tenant_id, name) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_llm_credentials_tenant_enabled "
        "ON llm_credentials (tenant_id, enabled) WHERE deleted_at IS NULL"
    )

    # ----- RLS on llm_credentials (mcp_servers / Deployments pattern) -----
    op.execute("ALTER TABLE llm_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE llm_credentials FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON llm_credentials FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON llm_credentials "
        "TO vibecanvas_app"
    )


def downgrade():
    op.execute("REVOKE ALL ON llm_credentials FROM vibecanvas_app")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON llm_credentials")
    op.execute("ALTER TABLE llm_credentials NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE llm_credentials DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_llm_credentials_tenant_enabled")
    op.execute("DROP INDEX IF EXISTS ix_llm_credentials_tenant_name")
