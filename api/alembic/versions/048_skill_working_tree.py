"""Repair/complete the durable Skill working-tree schema.

Revision ID: 048
Revises: 047
Create Date: 2026-07-24
"""

from alembic import op


revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 047 is still pre-release, but some developer databases may already have
    # applied an earlier draft of it. Keep this migration able to converge both
    # that shape and a fresh database on the same schema.
    op.execute("ALTER TABLE skill_revisions ADD COLUMN IF NOT EXISTS version integer")
    op.execute(
        """
        WITH numbered AS (
            SELECT revision_id,
                   row_number() OVER (
                       PARTITION BY skill_id ORDER BY created_at, revision_id
                   )::integer AS inferred_version
            FROM skill_revisions
        )
        UPDATE skill_revisions r
        SET version = numbered.inferred_version
        FROM numbered
        WHERE r.revision_id = numbered.revision_id AND r.version IS NULL
        """
    )
    op.execute("ALTER TABLE skill_revisions ALTER COLUMN version SET NOT NULL")
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_skill_revision_version'
            ) THEN
                ALTER TABLE skill_revisions
                ADD CONSTRAINT uq_skill_revision_version UNIQUE (skill_id, version);
            END IF;
        END $$;
        """
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON skill_revision_files")
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON skill_drafts")
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON skill_draft_files")
    op.execute(
        "CREATE POLICY tenant_isolation ON skill_draft_files FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON skill_draft_files TO vibecanvas_app"
    )
    # Developer databases that applied the earlier 047 draft may retain these
    # obsolete VFS-history columns. New writes ignore them; nullable keeps the
    # database-backed revision model authoritative without deleting local data.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='skill_revisions' AND column_name='vfs_scope_id'
            ) THEN
                ALTER TABLE skill_revisions
                    ALTER COLUMN vfs_scope_id DROP NOT NULL,
                    ALTER COLUMN vfs_prefix DROP NOT NULL;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name='skill_drafts' AND column_name='vfs_scope_id'
            ) THEN
                ALTER TABLE skill_drafts
                    ALTER COLUMN vfs_scope_id DROP NOT NULL,
                    ALTER COLUMN vfs_prefix DROP NOT NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS skill_draft_files")
    op.execute("DROP TABLE IF EXISTS skill_drafts")
    op.execute("DROP TABLE IF EXISTS skill_revision_files")
    # version is also part of the corrected 047 fresh schema, so a downgrade to
    # 047 intentionally leaves that column and constraint in place.
