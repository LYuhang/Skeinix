"""skills + skill_files — tenant-scoped skill bundles + FORCE RLS + grants type.

Revision ID: 025
Revises: 024
Create Date: 2026-06-13

A "skill" is a reusable bundle (a SKILL.md + optional files) an agent can load.
This mirrors the ``mcp_servers`` tenant-resource pattern (migration 006): a
soft-deletable, per-tenant table guarded by FORCE ROW LEVEL SECURITY + a
``tenant_isolation`` policy, plus a partial-unique index on
``(tenant_id, name) WHERE deleted_at IS NULL``. Bundle bytes live in the
ObjectStore (``skills/{tenant}/{skill}/{path}``); ``skill_files`` only records
the object key + metadata per path.

Self-contained (does NOT rely on 001's create_all): both tables are created here
via ``CREATE TABLE IF NOT EXISTS`` so the migration works on a pre-existing DB
(001's create_all only covers DBs created after these models existed — see
migration 021/022's lesson). Idempotent on every DB shape.

Also widens ``resource_grants.ck_grant_resource`` to admit a ``'skill'`` resource
type so skills can be shared through the existing grant mechanism.
"""
from alembic import op

revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


_RLS_TABLES = ("skills", "skill_files")


def upgrade():
    # ----- Tables (self-contained; create_all may not have run on old DBs) -----
    op.execute("""
        CREATE TABLE IF NOT EXISTS skills (
            skill_id      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id     uuid NOT NULL,
            user_id       uuid NOT NULL,
            name          text NOT NULL,
            description   text NOT NULL DEFAULT '',
            version       integer NOT NULL DEFAULT 1,
            allowed_tools jsonb NOT NULL DEFAULT '[]'::jsonb,
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            deleted_at    timestamptz
        )""")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_skill_tenant_name "
        "ON skills (tenant_id, name) WHERE deleted_at IS NULL"
    )

    op.execute("""
        CREATE TABLE IF NOT EXISTS skill_files (
            skill_id     uuid NOT NULL REFERENCES skills(skill_id) ON DELETE CASCADE,
            tenant_id    uuid NOT NULL,
            path         text NOT NULL,
            content_type text,
            object_key   text NOT NULL,
            size_bytes   integer NOT NULL DEFAULT 0,
            PRIMARY KEY (skill_id, path)
        )""")

    # ----- RLS on both tables (mirrors migration 006) -----
    for t in _RLS_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO vibecanvas_app")

    # ----- Widen resource_grants CHECK to admit 'skill' -----
    # ``DROP CONSTRAINT IF EXISTS``: on a fresh DB, 001's create_all builds
    # resource_grants from the ORM model (which carries only the UNIQUE
    # constraint, not the CHECK — that lives in migration 022's CREATE TABLE,
    # skipped under create_all's IF NOT EXISTS). So the constraint may not yet
    # exist; ADD it unconditionally afterward so the final state is uniform.
    op.execute(
        "ALTER TABLE resource_grants "
        "DROP CONSTRAINT IF EXISTS ck_grant_resource, "
        "ADD CONSTRAINT ck_grant_resource CHECK "
        "(resource_type IN ('template','workflow','task','deployment','skill'))"
    )


def downgrade():
    op.execute(
        "ALTER TABLE resource_grants "
        "DROP CONSTRAINT IF EXISTS ck_grant_resource, "
        "ADD CONSTRAINT ck_grant_resource CHECK "
        "(resource_type IN ('template','workflow','task','deployment'))"
    )
    for t in _RLS_TABLES:
        op.execute(f"REVOKE ALL ON {t} FROM vibecanvas_app")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TABLE IF EXISTS skill_files")
    op.execute("DROP INDEX IF EXISTS uq_skill_tenant_name")
    op.execute("DROP TABLE IF EXISTS skills")
