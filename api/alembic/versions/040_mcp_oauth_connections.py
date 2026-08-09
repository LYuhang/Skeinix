"""MCP catalog identity and per-user OAuth connections.

Revision ID: 040
Revises: 039
Create Date: 2026-07-17
"""
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None


def _tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO vibecanvas_app")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE mcp_servers
        ADD COLUMN IF NOT EXISTS source text,
        ADD COLUMN IF NOT EXISTS source_id text,
        ADD COLUMN IF NOT EXISTS source_url text,
        ADD COLUMN IF NOT EXISTS auth_mode text NOT NULL DEFAULT 'none',
        ADD COLUMN IF NOT EXISTS auth_metadata_url text,
        ADD COLUMN IF NOT EXISTS connection_status text NOT NULL DEFAULT 'not_required'
    """)
    op.execute("""
        ALTER TABLE mcp_servers
        DROP CONSTRAINT IF EXISTS ck_mcp_servers_auth_mode,
        DROP CONSTRAINT IF EXISTS ck_mcp_servers_connection_status,
        ADD CONSTRAINT ck_mcp_servers_auth_mode
            CHECK (auth_mode IN ('none','configuration','connection_discovery','oauth')),
        ADD CONSTRAINT ck_mcp_servers_connection_status
            CHECK (connection_status IN ('not_required','connection_required','connecting','connected','reconnect_required','connection_failed'))
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_mcp_servers_tenant_source_live
        ON mcp_servers (tenant_id, source, source_id)
        WHERE deleted_at IS NULL AND source IS NOT NULL AND source_id IS NOT NULL
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS mcp_oauth_connections (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
            authorization_server text NOT NULL,
            token_endpoint text NOT NULL,
            revocation_endpoint text,
            resource text NOT NULL,
            client_id text NOT NULL,
            client_secret_encrypted text,
            access_token_encrypted text NOT NULL,
            refresh_token_encrypted text,
            token_type text NOT NULL DEFAULT 'Bearer',
            scope text,
            expires_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_mcp_oauth_connections_server UNIQUE (server_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS mcp_oauth_transactions (
            state_hash text PRIMARY KEY,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
            code_verifier_encrypted text NOT NULL,
            redirect_uri text NOT NULL,
            return_origin text NOT NULL,
            authorization_server text NOT NULL,
            token_endpoint text NOT NULL,
            revocation_endpoint text,
            resource text NOT NULL,
            client_id text NOT NULL,
            client_secret_encrypted text,
            scope text,
            expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_mcp_oauth_transactions_expiry ON mcp_oauth_transactions (expires_at)")
    _tenant_rls("mcp_oauth_connections")
    _tenant_rls("mcp_oauth_transactions")


def downgrade() -> None:
    for table in ("mcp_oauth_transactions", "mcp_oauth_connections"):
        op.execute(f"REVOKE ALL ON {table} FROM vibecanvas_app")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS mcp_oauth_transactions")
    op.execute("DROP TABLE IF EXISTS mcp_oauth_connections")
    op.execute("DROP INDEX IF EXISTS uq_mcp_servers_tenant_source_live")
    op.execute("ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_connection_status")
    op.execute("ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS ck_mcp_servers_auth_mode")
    op.execute("""
        ALTER TABLE mcp_servers
        DROP COLUMN IF EXISTS connection_status,
        DROP COLUMN IF EXISTS auth_metadata_url,
        DROP COLUMN IF EXISTS auth_mode,
        DROP COLUMN IF EXISTS source_url,
        DROP COLUMN IF EXISTS source_id,
        DROP COLUMN IF EXISTS source
    """)
