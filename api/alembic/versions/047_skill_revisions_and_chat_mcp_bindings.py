"""User database Skill revisions and durable Chat MCP bindings.

Revision ID: 047
Revises: 046
Create Date: 2026-07-24
"""

from alembic import op


revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE chats ADD COLUMN IF NOT EXISTS "
        "mcp_config_revision bigint NOT NULL DEFAULT 0"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_mcp_bindings (
            chat_id text NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            mcp_server_id uuid NOT NULL REFERENCES mcp_servers(id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL DEFAULT
                current_setting('app.tenant_id', true)::uuid
                REFERENCES tenants(tenant_id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (chat_id, mcp_server_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_mcp_bindings_server "
        "ON chat_mcp_bindings (mcp_server_id)"
    )
    op.execute("ALTER TABLE chat_mcp_bindings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE chat_mcp_bindings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON chat_mcp_bindings FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON chat_mcp_bindings TO vibecanvas_app"
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_revisions (
            revision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            skill_id uuid NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            revision_hash text NOT NULL,
            version integer NOT NULL,
            file_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
            size_bytes integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_skill_revision_hash UNIQUE (skill_id, revision_hash),
            CONSTRAINT uq_skill_revision_version UNIQUE (skill_id, version)
        )
        """
    )
    op.execute("ALTER TABLE skill_revisions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE skill_revisions FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON skill_revisions FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON skill_revisions TO vibecanvas_app"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_revision_files (
            revision_id uuid NOT NULL
                REFERENCES skill_revisions(revision_id) ON DELETE CASCADE,
            path text NOT NULL,
            skill_id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            content_type text NOT NULL,
            content_hash text NOT NULL,
            size_bytes integer NOT NULL,
            content bytea NOT NULL,
            PRIMARY KEY (revision_id, path)
        )
        """
    )
    op.execute("ALTER TABLE skill_revision_files ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE skill_revision_files FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON skill_revision_files FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON skill_revision_files TO vibecanvas_app"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_drafts (
            skill_id uuid PRIMARY KEY REFERENCES skills(skill_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            base_revision_id uuid NOT NULL
                REFERENCES skill_revisions(revision_id),
            draft_hash text NOT NULL,
            file_manifest jsonb NOT NULL DEFAULT '[]'::jsonb,
            size_bytes integer NOT NULL DEFAULT 0,
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE skill_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE skill_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON skill_drafts FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON skill_drafts TO vibecanvas_app"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_draft_files (
            skill_id uuid NOT NULL
                REFERENCES skill_drafts(skill_id) ON DELETE CASCADE,
            path text NOT NULL,
            tenant_id uuid NOT NULL,
            user_id uuid NOT NULL,
            content_type text NOT NULL,
            content_hash text NOT NULL,
            size_bytes integer NOT NULL,
            content bytea NOT NULL,
            PRIMARY KEY (skill_id, path)
        )
        """
    )
    op.execute("ALTER TABLE skill_draft_files ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE skill_draft_files FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON skill_draft_files FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON skill_draft_files TO vibecanvas_app"
    )
    op.execute(
        "ALTER TABLE skills ADD COLUMN IF NOT EXISTS current_revision_id uuid"
    )
    op.execute(
        "ALTER TABLE skills DROP CONSTRAINT IF EXISTS "
        "skills_current_revision_id_fkey"
    )
    op.execute(
        "ALTER TABLE skills ADD CONSTRAINT skills_current_revision_id_fkey "
        "FOREIGN KEY (current_revision_id) REFERENCES skill_revisions(revision_id)"
    )
    op.execute("DROP INDEX IF EXISTS uq_skill_tenant_name")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_user_name "
        "ON skills (tenant_id, user_id, lower(name)) WHERE deleted_at IS NULL"
    )
    # Pre-release architectural replacement: versioned Skill bytes now live in
    # the revision tables above. Do not retain the legacy ObjectStore file index;
    # VFS is only a replaceable projection of the latest published revision.
    op.execute("DROP TABLE IF EXISTS skill_files")


def downgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS skill_files (
            skill_id uuid NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL,
            path text NOT NULL,
            content_type text,
            object_key text NOT NULL,
            size_bytes integer NOT NULL DEFAULT 0,
            PRIMARY KEY (skill_id, path)
        )
        """
    )
    op.execute("DROP INDEX IF EXISTS uq_skill_user_name")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_tenant_name "
        "ON skills (tenant_id, name) WHERE deleted_at IS NULL"
    )
    op.execute(
        "ALTER TABLE skills DROP CONSTRAINT IF EXISTS "
        "skills_current_revision_id_fkey"
    )
    op.execute("ALTER TABLE skills DROP COLUMN IF EXISTS current_revision_id")
    op.execute("DROP TABLE IF EXISTS skill_draft_files")
    op.execute("DROP TABLE IF EXISTS skill_drafts")
    op.execute("DROP TABLE IF EXISTS skill_revision_files")
    op.execute("DROP TABLE IF EXISTS skill_revisions")
    op.execute("DROP TABLE IF EXISTS chat_mcp_bindings")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS mcp_config_revision")
