"""Immutable Chat runtime binding and user runtime defaults.

Revision ID: 043
Revises: 042
Create Date: 2026-07-21
"""

from alembic import op


revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_type TEXT")
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_session_id TEXT")
    op.execute("ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_state_ref TEXT")
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS runtime_version "
        "INTEGER NOT NULL DEFAULT 1"
    )
    op.execute("""
        DO $$ BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_chats_runtime_type'
            ) THEN
                ALTER TABLE chats ADD CONSTRAINT ck_chats_runtime_type
                CHECK (runtime_type IS NULL OR runtime_type IN ('langchain','codex'));
            END IF;
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_chats_runtime_version_pos'
            ) THEN
                ALTER TABLE chats ADD CONSTRAINT ck_chats_runtime_version_pos
                CHECK (runtime_version > 0);
            END IF;
        END $$;
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_agent_preferences (
            user_id UUID PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
            tenant_id UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE
                DEFAULT current_setting('app.tenant_id', true)::uuid,
            default_runtime_type TEXT NOT NULL DEFAULT 'langchain',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_user_agent_preferences_runtime_type
                CHECK (default_runtime_type IN ('langchain','codex'))
        )
    """)
    op.execute("ALTER TABLE user_agent_preferences ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE user_agent_preferences FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON user_agent_preferences")
    op.execute("""
        CREATE POLICY tenant_isolation ON user_agent_preferences FOR ALL
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
    """)
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON user_agent_preferences TO vibecanvas_app"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON user_agent_preferences")
    op.execute("DROP TABLE IF EXISTS user_agent_preferences CASCADE")
    op.execute("ALTER TABLE chats DROP CONSTRAINT IF EXISTS ck_chats_runtime_version_pos")
    op.execute("ALTER TABLE chats DROP CONSTRAINT IF EXISTS ck_chats_runtime_type")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_version")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_state_ref")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_session_id")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS runtime_type")
