"""mcp_servers — FORCE RLS + tenant policy + partial indexes.

Revision ID: 006
Revises: 005
Create Date: 2026-05-25

The ``mcp_servers`` table itself is created by migration 001's
``Base.metadata.create_all(bind)``, which mirrors the current models
(``McpServer`` registers via ``storage/models.py``'s tail-import of
``models_mcp_servers``). This migration only adds what ``create_all``
cannot express:

  - partial UNIQUE / partial regular indexes on ``mcp_servers``
  - row-level security (ENABLE + FORCE) + tenant_isolation policy

Pattern mirrors migration 005 (Deployments). GRANT is included to be
explicit — owner already has DML implicitly, but mirroring the spec
keeps role-scope review uniform.
"""
from alembic import op

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    # ----- Partial indexes on mcp_servers -----
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_mcp_servers_tenant_name "
        "ON mcp_servers (tenant_id, name) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_mcp_servers_tenant_prefix "
        "ON mcp_servers (tenant_id, tool_prefix) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_mcp_servers_tenant_enabled "
        "ON mcp_servers (tenant_id, enabled) WHERE deleted_at IS NULL"
    )

    # ----- RLS on mcp_servers (Phase 5 / Phase 6 T2 / Deployments T1 pattern) -----
    op.execute("ALTER TABLE mcp_servers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_servers FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON mcp_servers FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON mcp_servers TO vibecanvas_app"
    )


def downgrade():
    op.execute("REVOKE ALL ON mcp_servers FROM vibecanvas_app")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON mcp_servers")
    op.execute("ALTER TABLE mcp_servers NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE mcp_servers DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_mcp_servers_tenant_enabled")
    op.execute("DROP INDEX IF EXISTS ix_mcp_servers_tenant_prefix")
    op.execute("DROP INDEX IF EXISTS ix_mcp_servers_tenant_name")
