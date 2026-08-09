"""phase 5 — enable RLS + tenant-isolation policies on the business tables

Revision ID: 003
Revises: 002

The business tables' final column shape (UUID tenant_id / creator_user_id,
templates.visibility) is produced by migration 001's Base.metadata.create_all,
which always reflects the current models. This migration only adds what
create_all cannot express: row-level security.
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

_BIZ = ["workflows", "workflow_versions", "chats", "chat_messages",
        "templates"]


def upgrade():
    for t in _BIZ:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")
    for t in [x for x in _BIZ if x != "templates"]:
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL "
            f"USING (tenant_id = current_setting('app.tenant_id',true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id',true)::uuid);")
    op.execute(
        "CREATE POLICY tpl_read ON templates FOR SELECT "
        "USING (tenant_id = current_setting('app.tenant_id',true)::uuid "
        "       OR visibility = 'public');")
    op.execute(
        "CREATE POLICY tpl_write ON templates FOR ALL "
        "USING (tenant_id = current_setting('app.tenant_id',true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id',true)::uuid);")


def downgrade():
    for t in [x for x in _BIZ if x != "templates"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t};")
    op.execute("DROP POLICY IF EXISTS tpl_read ON templates;")
    op.execute("DROP POLICY IF EXISTS tpl_write ON templates;")
    for t in _BIZ:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")
