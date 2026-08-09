"""org permissions foundation — orgs/departments/memberships/grants + owner_id.

Revision ID: 022
Revises: 021
Create Date: 2026-06-11

Creates every table idempotently (CREATE TABLE IF NOT EXISTS) so it works on a
pre-existing DB (001's create_all only covers DBs created after these models
existed — see migration 021's lesson). RLS enabled + tenant policies added.
Backfill is appended to this file's upgrade() (Task 6).

Zero behavior change: the new tables/columns are additive, RLS stays keyed on
tenant_id, and no existing read policy is modified. Every existing user remains
the base case (personal singleton org, owner, no grants).
"""
from alembic import op

revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Backfill — idempotent (ON CONFLICT DO NOTHING / WHERE owner_id IS NULL). Kept
# as a module-level list so the smoke test can replay it against freshly-seeded
# rows (the migration itself runs once, against an empty DB, at session start).
#
# owner_id sources, confirmed against the real schema:
#   - workflows : creator_user_id (existing column)
#   - templates : creator_user_id (existing column)
#   - tasks     : user_id (the creator; existing column)
#   - deployments: user_id (the creator; existing column)
# Public templates (visibility='public') become a (template, public, viewer) grant.
# ---------------------------------------------------------------------------
BACKFILL_SQL = [
    # 1. Every existing tenant becomes a personal org. slug derived from id.
    """
    INSERT INTO organizations (tenant_id, kind, slug, name)
    SELECT t.tenant_id, 'personal',
           'org-' || replace(t.tenant_id::text, '-', ''),
           COALESCE(NULLIF(t.name, ''), 'Personal')
    FROM tenants t
    ON CONFLICT (tenant_id) DO NOTHING
    """,
    # 2. Each user owns their tenant's org.
    """
    INSERT INTO org_memberships (membership_id, user_id, tenant_id, org_role)
    SELECT gen_random_uuid(), u.user_id, u.tenant_id, 'owner'
    FROM users u
    ON CONFLICT (user_id, tenant_id) DO NOTHING
    """,
    # 3. owner_id backfill from each resource's existing creator column.
    "UPDATE workflows SET owner_id = creator_user_id "
    "WHERE owner_id IS NULL AND creator_user_id IS NOT NULL",
    "UPDATE templates SET owner_id = creator_user_id "
    "WHERE owner_id IS NULL AND creator_user_id IS NOT NULL",
    "UPDATE tasks SET owner_id = user_id "
    "WHERE owner_id IS NULL AND user_id IS NOT NULL",
    "UPDATE deployments SET owner_id = user_id "
    "WHERE owner_id IS NULL AND user_id IS NOT NULL",
    # 4. Existing public templates → a public viewer grant (resource_id is text).
    """
    INSERT INTO resource_grants
        (grant_id, tenant_id, resource_type, resource_id, principal_type, principal_id, level, created_by)
    SELECT gen_random_uuid(), tpl.tenant_id, 'template', tpl.template_id, 'public', NULL, 'viewer',
           COALESCE(tpl.owner_id, tpl.creator_user_id)
    FROM templates tpl
    WHERE tpl.visibility = 'public'
    ON CONFLICT (resource_type, resource_id, principal_type, principal_id) DO NOTHING
    """,
]


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")

    op.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            tenant_id  uuid PRIMARY KEY REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            kind       text NOT NULL DEFAULT 'personal',
            slug       text NOT NULL UNIQUE,
            name       text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_org_kind CHECK (kind IN ('personal','business'))
        )""")

    op.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            dept_id        uuid PRIMARY KEY,
            tenant_id      uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            parent_dept_id uuid REFERENCES departments(dept_id) ON DELETE CASCADE,
            path           ltree NOT NULL,
            name           text NOT NULL,
            created_at     timestamptz NOT NULL DEFAULT now()
        )""")
    # If migration 001's create_all already built `departments` from the ORM
    # model, `path` is TEXT (SQLAlchemy has no ltree type) — coerce it to the
    # real ltree type so the GIST index below works. No-op when 022 created the
    # table (path is already ltree; the USING cast is harmless).
    op.execute("ALTER TABLE departments ALTER COLUMN path TYPE ltree USING path::ltree")
    op.execute("CREATE INDEX IF NOT EXISTS ix_departments_path ON departments USING gist (path)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_departments_tenant ON departments (tenant_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS org_memberships (
            membership_id uuid PRIMARY KEY,
            user_id   uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            org_role  text NOT NULL DEFAULT 'member',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_org_membership UNIQUE (user_id, tenant_id),
            CONSTRAINT ck_org_role CHECK (org_role IN ('owner','admin','member'))
        )""")

    op.execute("""
        CREATE TABLE IF NOT EXISTS dept_memberships (
            membership_id uuid PRIMARY KEY,
            user_id   uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            dept_id   uuid NOT NULL REFERENCES departments(dept_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            dept_role text NOT NULL DEFAULT 'member',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_dept_membership UNIQUE (user_id, dept_id),
            CONSTRAINT ck_dept_role CHECK (dept_role IN ('lead','member'))
        )""")

    op.execute("""
        CREATE TABLE IF NOT EXISTS resource_grants (
            grant_id      uuid PRIMARY KEY,
            tenant_id     uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            resource_type text NOT NULL,
            resource_id   text NOT NULL,  -- text: resource PKs are text (wf_id, template_id)
            principal_type text NOT NULL,
            principal_id  uuid,
            level         text NOT NULL,
            created_by    uuid NOT NULL REFERENCES users(user_id),
            created_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_resource_grant UNIQUE (resource_type, resource_id, principal_type, principal_id),
            CONSTRAINT ck_grant_resource CHECK (resource_type IN ('template','workflow','task','deployment')),
            CONSTRAINT ck_grant_principal CHECK (principal_type IN ('user','department','organization','public')),
            CONSTRAINT ck_grant_level CHECK (level IN ('viewer','editor','manager'))
        )""")
    op.execute("CREATE INDEX IF NOT EXISTS ix_grants_principal "
               "ON resource_grants (principal_type, principal_id, resource_type)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_grants_public "
               "ON resource_grants (resource_type, resource_id) WHERE principal_type='public'")

    # owner_id on shareable resources (nullable; backfilled in Task 6)
    for t in ("templates", "workflows", "tasks", "deployments"):
        op.execute(f"ALTER TABLE {t} ADD COLUMN IF NOT EXISTS owner_id uuid REFERENCES users(user_id)")

    # ---- RLS: tenant-scoped on all five; resource_grants additionally exposes
    #      public rows for reads (the §3 self-reference fix). Writes stay tenant-scoped.
    for t in ("organizations", "departments", "org_memberships", "dept_memberships"):
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO vibecanvas_app")

    op.execute("ALTER TABLE resource_grants ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE resource_grants FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY grants_read ON resource_grants FOR SELECT "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid "
        "       OR principal_type = 'public')")
    op.execute(
        "CREATE POLICY grants_write ON resource_grants FOR INSERT "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute(
        "CREATE POLICY grants_modify ON resource_grants FOR UPDATE "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute(
        "CREATE POLICY grants_delete ON resource_grants FOR DELETE "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid)")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON resource_grants TO vibecanvas_app")

    # ---- Backfill existing data so every current user is a valid base case
    #      (personal singleton org, owner, owner_id set, public templates → grants).
    #
    # alembic runs as the NON-superuser table-OWNER role (vibecanvas_app). Under
    # FORCE ROW LEVEL SECURITY even the owner is subject to RLS, so these
    # cross-tenant writes (no single app.tenant_id covers all tenants) would fail
    # with "new row violates row-level security policy" (new-table INSERTs) or
    # silently match 0 rows (existing-table owner_id UPDATEs, where app.tenant_id
    # is unset). The owner BYPASSES RLS when FORCE is OFF, so disable FORCE on
    # every table the backfill touches, run it, then restore FORCE — all inside
    # this one transactional migration. Owner-only ALTERs; the tables' final RLS
    # state is unchanged.
    _backfill_tables = (
        "organizations", "org_memberships", "resource_grants",
        "templates", "workflows", "tasks", "deployments",
    )
    for t in _backfill_tables:
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
    for stmt in BACKFILL_SQL:
        op.execute(stmt)
    for t in _backfill_tables:
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")


def downgrade():
    for t in ("resource_grants", "dept_memberships", "org_memberships",
              "departments", "organizations"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    for t in ("templates", "workflows", "tasks", "deployments"):
        op.execute(f"ALTER TABLE {t} DROP COLUMN IF EXISTS owner_id")
