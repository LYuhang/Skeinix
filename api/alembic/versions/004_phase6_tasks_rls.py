"""phase 6 — FORCE RLS + tenant-isolation policy on tasks + task_events

Revision ID: 004
Revises: 003

The table definitions are created by migration 001's
``Base.metadata.create_all(bind)``, which mirrors the current models
(now including Task + TaskEvent from ``storage/models_tasks.py``).
This migration only adds what create_all cannot express: row-level
security and FORCE on the two new tenant-scoped tables.

GRANTs are intentionally omitted: per conftest._migrate the alembic
upgrade runs AS ``vibecanvas_app`` (the app role), which means
``create_all`` makes that role the OWNER of the new tables — owners
already hold DML implicitly. This mirrors migration 003's pattern
(no GRANT lines there either).
"""
from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None

_TASK_TABLES = ["tasks", "task_events"]


def upgrade():
    for t in _TASK_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY;")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);"
        )


def downgrade():
    for t in _TASK_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t};")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY;")
