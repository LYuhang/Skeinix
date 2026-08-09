"""Secure Web cookies and one-time extension session exchange.

Revision ID: 058
Revises: 057
Create Date: 2026-07-31
"""

from alembic import op


revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS audience text NOT NULL DEFAULT 'web'"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS parent_session_id uuid"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS csrf_token_hash text"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_audience"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT ck_sessions_audience "
        "CHECK (audience IN ('web','extension','api'))"
    )
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "fk_sessions_parent_session"
    )
    op.execute(
        "ALTER TABLE sessions ADD CONSTRAINT fk_sessions_parent_session "
        "FOREIGN KEY(parent_session_id) REFERENCES sessions(session_id) "
        "ON DELETE CASCADE"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sessions_parent_session "
        "ON sessions(parent_session_id)"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS session_exchange_codes ("
        " code_hash text PRIMARY KEY,"
        " parent_session_id uuid NOT NULL REFERENCES sessions(session_id) "
        "   ON DELETE CASCADE,"
        " user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,"
        " tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) "
        "   ON DELETE CASCADE,"
        " audience text NOT NULL DEFAULT 'extension',"
        " created_at timestamptz NOT NULL DEFAULT now(),"
        " expires_at timestamptz NOT NULL,"
        " CONSTRAINT ck_session_exchange_codes_audience "
        "   CHECK (audience = 'extension')"
        ")"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_session_exchange_codes_expires "
        "ON session_exchange_codes(expires_at)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS session_exchange_codes")
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS "
        "fk_sessions_parent_session"
    )
    op.execute("DROP INDEX IF EXISTS ix_sessions_parent_session")
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_audience"
    )
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS csrf_token_hash")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS parent_session_id")
    op.execute("ALTER TABLE sessions DROP COLUMN IF EXISTS audience")
