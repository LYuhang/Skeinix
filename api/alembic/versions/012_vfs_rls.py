"""vfs — vfs_artifacts + vfs_scratch FORCE RLS + tenant GUC default + policy.

Revision ID: 012
Revises: 011
Create Date: 2026-06-01

Both tables are created by migration 001's create_all (the models VfsArtifact /
VfsScratch register via storage/models.py). This migration adds only the RLS
layer create_all can't express (same RLS-only style as 003-010).
"""
from alembic import op

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None

_TABLES = ("vfs_artifacts", "vfs_scratch")


def upgrade() -> None:
    for t in _TABLES:
        op.execute(f"ALTER TABLE {t} ALTER COLUMN tenant_id "
                   f"SET DEFAULT current_setting('app.tenant_id', true)::uuid")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {t}_tenant ON {t} FOR ALL "
            f"USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO vibecanvas_app")


def downgrade() -> None:
    for t in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {t}_tenant ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} ALTER COLUMN tenant_id DROP DEFAULT")
