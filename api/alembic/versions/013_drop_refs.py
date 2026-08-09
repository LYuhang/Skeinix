"""drop the refs table (VFS 2b-3 — ref store deleted).

Revision ID: 013
Revises: 012
"""
from alembic import op

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade():
    # already-migrated DBs: drop the policy + table.
    # fresh DBs (post Ref-model deletion): 001's create_all no longer makes
    # refs, so IF EXISTS makes both a safe no-op. Both guards are mandatory.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON refs;")
    op.execute("DROP TABLE IF EXISTS refs CASCADE;")


def downgrade():
    # recreate an EMPTY refs mirroring the (now-deleted) Ref model EXACTLY.
    # Hand-written DDL — every column + default is load-bearing for the
    # up->down->up round-trip and RLS-insert correctness. NO grants (mirror 003:
    # migrations run AS vibecanvas_app which owns the table + keeps implicit DML).
    op.execute("""
        CREATE TABLE refs (
            ref_id      TEXT PRIMARY KEY,
            chat_id     TEXT NOT NULL REFERENCES chats(chat_id) ON DELETE CASCADE,
            ref_type    TEXT NOT NULL,
            content     JSONB NOT NULL,
            abstract    TEXT NOT NULL DEFAULT '',
            meta        JSONB NOT NULL DEFAULT '{}',
            size_bytes  INTEGER NOT NULL DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_access TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id   UUID NOT NULL REFERENCES tenants(tenant_id)
                        DEFAULT current_setting('app.tenant_id', true)::uuid
        );
    """)
    op.execute("CREATE INDEX ix_refs_chat ON refs (chat_id);")
    op.execute("ALTER TABLE refs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE refs FORCE ROW LEVEL SECURITY;")
    op.execute(
        "CREATE POLICY tenant_isolation ON refs FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id',true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id',true)::uuid);")
