"""Remove retired secret columns and enforce SecretService references.

Revision ID: 073
Revises: 072
Create Date: 2026-07-31

This cutover is intentionally irreversible. The host migration command must
first move every old value into ``encrypted_secrets``. Runtime binaries never
dual-read old columns.
"""
from alembic import op


revision = "073"
down_revision = "072"
branch_labels = None
depends_on = None


def _guard_old_column(
    table: str,
    column: str,
    predicate: str,
    message: str,
) -> None:
    escaped_predicate = predicate.replace("'", "''")
    escaped_message = message.replace("'", "''")
    op.execute(
        "DO $$ DECLARE invalid_rows boolean; BEGIN "
        "IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=current_schema() "
        f"AND table_name='{table}' AND column_name='{column}') THEN "
        f"EXECUTE 'SELECT EXISTS (SELECT 1 FROM {table} WHERE "
        f"{escaped_predicate})' INTO invalid_rows; "
        f"IF invalid_rows THEN RAISE EXCEPTION '{escaped_message}'; END IF; "
        "END IF; END $$"
    )


def upgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM llm_credentials WHERE "
        "secret_ref IS NULL) THEN RAISE EXCEPTION "
        "'LLM credential secret migration incomplete'; END IF; END $$"
    )
    _guard_old_column(
        "llm_credentials", "api_key", "api_key IS NOT NULL",
        "LLM credential plaintext migration incomplete",
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mcp_oauth_connections WHERE "
        "secret_ref IS NULL) "
        "THEN RAISE EXCEPTION 'MCP OAuth connection migration incomplete'; "
        "END IF; END $$"
    )
    for column in (
        "client_secret_encrypted",
        "access_token_encrypted",
        "refresh_token_encrypted",
    ):
        _guard_old_column(
            "mcp_oauth_connections", column, f"{column} IS NOT NULL",
            "MCP OAuth connection plaintext migration incomplete",
        )
    op.execute(
        "DELETE FROM mcp_oauth_transactions WHERE expires_at <= now()"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mcp_oauth_transactions WHERE "
        "secret_ref IS NULL) THEN RAISE EXCEPTION "
        "'MCP OAuth transaction migration incomplete'; END IF; END $$"
    )
    for column in ("code_verifier_encrypted", "client_secret_encrypted"):
        _guard_old_column(
            "mcp_oauth_transactions", column, f"{column} IS NOT NULL",
            "MCP OAuth transaction plaintext migration incomplete",
        )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM deployments WHERE "
        "trigger_type='webhook' AND hmac_secret_ref IS NULL) "
        "THEN RAISE EXCEPTION "
        "'Deployment webhook secret migration incomplete'; END IF; END $$"
    )
    _guard_old_column(
        "deployments", "hmac_secret", "hmac_secret IS NOT NULL",
        "Deployment webhook plaintext migration incomplete",
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mcp_servers WHERE "
        "auth_config ? 'token' OR (auth_config->>'type'='bearer' AND "
        "auth_secret_ref IS NULL)) THEN RAISE EXCEPTION "
        "'MCP bearer secret migration incomplete'; END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM mcp_servers WHERE "
        "position('?' in endpoint)>0 OR "
        "position('?' in coalesce(connection_config->>'url',''))>0 OR "
        "coalesce(connection_config->'headers','{}'::jsonb)<>'{}'::jsonb OR "
        "coalesce(connection_config->'env','{}'::jsonb)<>'{}'::jsonb) "
        "THEN RAISE EXCEPTION 'MCP connection secret migration incomplete'; "
        "END IF; END $$"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM llm_credentials WHERE "
        "position('?' in coalesce(api_url,''))>0 OR "
        "position('?' in coalesce(proxy,''))>0 OR "
        "coalesce(api_url,'') ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@' OR "
        "coalesce(proxy,'') ~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@') "
        "THEN RAISE EXCEPTION 'LLM connection secret migration incomplete'; "
        "END IF; END $$"
    )

    op.execute("ALTER TABLE llm_credentials ALTER COLUMN secret_ref SET NOT NULL")
    op.execute(
        "ALTER TABLE mcp_oauth_connections ALTER COLUMN secret_ref SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE mcp_oauth_transactions ALTER COLUMN secret_ref SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE deployments DROP CONSTRAINT IF EXISTS "
        "ck_deployments_hmac_required"
    )
    op.execute(
        "ALTER TABLE deployments ADD CONSTRAINT ck_deployments_hmac_required "
        "CHECK ((trigger_type != 'webhook') OR (hmac_secret_ref IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS "
        "ck_mcp_auth_secret_reference"
    )
    op.execute(
        "ALTER TABLE mcp_servers ADD CONSTRAINT ck_mcp_auth_secret_reference "
        "CHECK (NOT (auth_config ? 'token') AND "
        "((auth_config->>'type' != 'bearer') OR auth_secret_ref IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE mcp_servers DROP CONSTRAINT IF EXISTS "
        "ck_mcp_connection_public_projection"
    )
    op.execute(
        "ALTER TABLE mcp_servers ADD CONSTRAINT ck_mcp_connection_public_projection "
        "CHECK (position('?' in endpoint)=0 AND "
        "position('?' in coalesce(connection_config->>'url',''))=0 AND "
        "coalesce(connection_config->'headers','{}'::jsonb)='{}'::jsonb AND "
        "coalesce(connection_config->'env','{}'::jsonb)='{}'::jsonb)"
    )
    op.execute(
        "ALTER TABLE llm_credentials DROP CONSTRAINT IF EXISTS "
        "ck_llm_connection_public_projection"
    )
    op.execute(
        "ALTER TABLE llm_credentials ADD CONSTRAINT "
        "ck_llm_connection_public_projection CHECK ("
        "position('?' in coalesce(api_url,''))=0 AND "
        "position('?' in coalesce(proxy,''))=0 AND "
        "coalesce(api_url,'') !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@' AND "
        "coalesce(proxy,'') !~ '^[a-zA-Z][a-zA-Z0-9+.-]*://[^/]*@')"
    )

    op.execute("ALTER TABLE llm_credentials DROP COLUMN IF EXISTS api_key")
    for column in (
        "client_secret_encrypted",
        "access_token_encrypted",
        "refresh_token_encrypted",
    ):
        op.execute(
            "ALTER TABLE mcp_oauth_connections "
            f"DROP COLUMN IF EXISTS {column}"
        )
    for column in ("code_verifier_encrypted", "client_secret_encrypted"):
        op.execute(
            "ALTER TABLE mcp_oauth_transactions "
            f"DROP COLUMN IF EXISTS {column}"
        )
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS hmac_secret")


def downgrade() -> None:
    raise RuntimeError(
        "revision 073 is intentionally irreversible: retired secret columns "
        "cannot be restored"
    )
