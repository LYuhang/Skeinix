"""Durable Diagram draft readiness and render revision cursors.

Revision ID: 113
Revises: 112
Create Date: 2026-08-06
"""

from alembic import op


revision = "113"
down_revision = "112"
branch_labels = None
depends_on = None


def upgrade() -> None:
    statements = (
        """
        CREATE TABLE IF NOT EXISTS diagram_drafts (
          draft_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', true)::uuid
            REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          owner_user_id UUID NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
          chat_id TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
          turn_id TEXT NOT NULL,
          workspace_scope_id TEXT NOT NULL,
          source_path TEXT NOT NULL,
          target_path TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'writing',
          latest_source_sequence BIGINT NOT NULL DEFAULT 0,
          latest_ready_sequence BIGINT NOT NULL DEFAULT 0,
          latest_ready_scene_ref TEXT,
          terminal BOOLEAN NOT NULL DEFAULT false,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CONSTRAINT ck_diagram_drafts_status CHECK (
            status IN ('writing','parsing','compiling','ready','invalid',
              'superseded','committed','cancelled')
          ),
          CONSTRAINT uq_diagram_drafts_turn_source UNIQUE (chat_id, turn_id, source_path)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_diagram_drafts_chat_updated
          ON diagram_drafts(chat_id, updated_at)
        """,
        """
        CREATE TABLE IF NOT EXISTS diagram_render_revisions (
          draft_id UUID NOT NULL REFERENCES diagram_drafts(draft_id) ON DELETE CASCADE,
          sequence BIGINT NOT NULL,
          revision_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL DEFAULT current_setting('app.tenant_id', true)::uuid
            REFERENCES tenants(tenant_id) ON DELETE CASCADE,
          status TEXT NOT NULL,
          operation TEXT NOT NULL DEFAULT 'update_diagram',
          element_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
          source_hash TEXT NOT NULL,
          scene_ref TEXT,
          scene_hash TEXT,
          scene_path TEXT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          PRIMARY KEY (draft_id, sequence),
          CONSTRAINT ck_diagram_render_revisions_status CHECK (
            status IN ('writing','parsing','compiling','ready','invalid',
              'superseded','committed','cancelled')
          )
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_diagram_render_revisions_ready_cursor
          ON diagram_render_revisions(tenant_id, draft_id, status, sequence)
        """,
        "ALTER TABLE diagram_drafts ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE diagram_drafts FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS diagram_drafts_tenant_isolation ON diagram_drafts",
        """
        CREATE POLICY diagram_drafts_tenant_isolation ON diagram_drafts
          USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """,
        "ALTER TABLE diagram_render_revisions ENABLE ROW LEVEL SECURITY",
        "ALTER TABLE diagram_render_revisions FORCE ROW LEVEL SECURITY",
        "DROP POLICY IF EXISTS diagram_render_revisions_tenant_isolation "
        "ON diagram_render_revisions",
        """
        CREATE POLICY diagram_render_revisions_tenant_isolation
          ON diagram_render_revisions
          USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
          WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """,
    )
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS diagram_render_revisions")
    op.execute("DROP TABLE IF EXISTS diagram_drafts")
