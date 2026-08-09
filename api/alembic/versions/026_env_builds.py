"""env_builds — GLOBAL content-addressed overlay build registry. NO RLS.

Revision ID: 026
Revises: 025
Create Date: 2026-06-17

``env_builds`` records one materialized pip overlay per content-hash
(``overlay_key`` = sha256 of the declared requirements). The overlay is pure
public-PyPI content shared across all tenants, so this table is DELIBERATELY
tenant-agnostic and carries NO row-level security — an INTENTIONAL exception to
the Phase 5 "every tenant table FORCE RLS" convention. There is no
``tenant_id`` and no ``tenant_isolation`` policy; a tenant only looks a row up
by its content-derived key.

Self-contained (does NOT rely on 001's create_all): the table is created here
via ``CREATE TABLE IF NOT EXISTS`` so the migration also lands on a pre-existing
DB (001's create_all only covers DBs created after this model existed — see
migrations 021/022/025's lesson). Idempotent on every DB shape.

GRANT DML to ``vibecanvas_app`` so the (non-superuser) app role can read/write
it — the same role-scope explicitness as the RLS migrations, just WITHOUT the
ENABLE/FORCE/POLICY lines.
"""
from alembic import op

revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


def upgrade():
    # ----- Table (self-contained; create_all may not have run on old DBs) -----
    op.execute("""
        CREATE TABLE IF NOT EXISTS env_builds (
            overlay_key   varchar(64) PRIMARY KEY,
            status        text NOT NULL,
            error_log     text,
            requirements  text NOT NULL,
            built_at      timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_env_builds_status
                CHECK (status IN ('building', 'ready', 'failed'))
        )""")
    # NO ENABLE/FORCE ROW LEVEL SECURITY + NO tenant_isolation policy: this is a
    # deliberate GLOBAL content-addressed cache (exception to the FORCE-RLS
    # convention). Only the DML grant for the app role, mirroring the other
    # migrations' explicit role scoping.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON env_builds TO vibecanvas_app"
    )


def downgrade():
    op.execute("REVOKE ALL ON env_builds FROM vibecanvas_app")
    op.execute("DROP TABLE IF EXISTS env_builds")
