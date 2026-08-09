"""vfs_run table + FORCE RLS (RE-1 — run-scoped ephemeral binary VFS tier).

Revision ID: 017
Revises: 016

Run-scoped metadata index: path/object_key/content_type/size, keyed by an
explicit run_id. Bytes live in the ObjectStore at object_key (NO inline content).
FORCE RLS by tenant + GUC default (same idiom as vfs_artifacts: migrations
001 create_all + 012 RLS, condensed into this single migration via an
idempotent CREATE so fresh DBs and already-migrated DBs converge).
"""
from alembic import op

revision = "017"
down_revision = "016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS vfs_run (
            run_id       TEXT NOT NULL,
            path         TEXT NOT NULL,
            object_key   TEXT NOT NULL,
            content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            size_bytes   INTEGER NOT NULL DEFAULT 0,
            abstract     TEXT NOT NULL DEFAULT '',
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_access  TIMESTAMPTZ NOT NULL DEFAULT now(),
            tenant_id    UUID NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            PRIMARY KEY (run_id, path)
        );
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vfs_run_scope "
        "ON vfs_run (tenant_id, run_id);"
    )
    op.execute("ALTER TABLE vfs_run ALTER COLUMN tenant_id "
               "SET DEFAULT current_setting('app.tenant_id', true)::uuid")
    op.execute("ALTER TABLE vfs_run ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE vfs_run FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY vfs_run_tenant ON vfs_run FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON vfs_run TO vibecanvas_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS vfs_run_tenant ON vfs_run")
    op.execute("DROP TABLE IF EXISTS vfs_run CASCADE")
