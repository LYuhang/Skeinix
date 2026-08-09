"""Server-owned active organizations, membership lifecycle, and generic groups.

Revision ID: 055
Revises: 054
Create Date: 2026-07-30

The physical ``tenant_id`` column remains the PostgreSQL RLS key. Sessions now
also name that value explicitly as ``active_organization_id`` and carry a
generation that is rotated on every organization switch. The old department
tables are migrated into the generic, bounded-adjacency group model and then
removed so there is only one organization hierarchy representation.
"""

from alembic import op


revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Migration 022 FORCEd RLS even for the table-owning application role.
    # This migration has to project existing rows across every organization,
    # so temporarily restore owner bypass inside this transactional migration.
    # All surviving tenant tables are FORCEd again before commit.
    for table in (
        "organizations",
        "org_memberships",
        "departments",
        "dept_memberships",
        "resource_grants",
    ):
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    # Revision 022 backfilled the tenants/users that existed when it ran, but
    # older application versions could continue creating tenants and users
    # before this revision was deployed. Reconcile that valid 022→054 data
    # shape before sessions gain an organization FK. Without this second,
    # idempotent projection an existing session can point at its tenant while
    # the corresponding organization row is absent, making the FK cutover
    # fail. Do not delete or retarget those sessions: preserving the personal
    # organization capability is the intended migration behavior.
    op.execute(
        """
        INSERT INTO organizations (tenant_id, kind, slug, name)
        SELECT
            tenant.tenant_id,
            'personal',
            'org-' || replace(tenant.tenant_id::text, '-', ''),
            COALESCE(NULLIF(tenant.name, ''), 'Personal')
        FROM tenants AS tenant
        ON CONFLICT (tenant_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO org_memberships (
            membership_id, user_id, tenant_id, org_role
        )
        SELECT
            gen_random_uuid(), user_row.user_id, user_row.tenant_id, 'owner'
        FROM users AS user_row
        ON CONFLICT (user_id, tenant_id) DO NOTHING
        """
    )

    # Organization metadata needed for business organizations and audit.
    op.execute(
        "ALTER TABLE organizations "
        "ADD COLUMN IF NOT EXISTS created_by uuid "
        "REFERENCES users(user_id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE organizations "
        "ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        """
        UPDATE organizations AS organization
        SET created_by = (
            SELECT membership.user_id
            FROM org_memberships AS membership
            WHERE membership.tenant_id = organization.tenant_id
              AND membership.org_role = 'owner'
            ORDER BY membership.created_at, membership.membership_id
            LIMIT 1
        )
        WHERE organization.created_by IS NULL
        """
    )

    # Membership state is explicit and fail-closed. ``invited`` is retained as
    # a lifecycle value even while the V1 company invitation UI remains off.
    op.execute(
        "ALTER TABLE org_memberships "
        "ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active'"
    )
    op.execute(
        "ALTER TABLE org_memberships "
        "ADD COLUMN IF NOT EXISTS invited_by uuid "
        "REFERENCES users(user_id) ON DELETE SET NULL"
    )
    op.execute(
        "ALTER TABLE org_memberships "
        "ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute(
        "ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS ck_org_role"
    )
    op.execute(
        """
        ALTER TABLE org_memberships
        ADD CONSTRAINT ck_org_role
        CHECK (org_role IN ('owner','admin','member','guest','auditor'))
        """
    )
    op.execute(
        "ALTER TABLE org_memberships "
        "DROP CONSTRAINT IF EXISTS ck_org_membership_status"
    )
    op.execute(
        """
        ALTER TABLE org_memberships
        ADD CONSTRAINT ck_org_membership_status
        CHECK (
            status IN (
                'invited','active','suspended','revoking','revoked'
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_org_memberships_user_status "
        "ON org_memberships(user_id, status, tenant_id)"
    )

    # A caller can discover only their own organization memberships before an
    # active organization is selected. All writes remain active-org scoped.
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON org_memberships")
    op.execute("DROP POLICY IF EXISTS org_memberships_select ON org_memberships")
    op.execute("DROP POLICY IF EXISTS org_memberships_insert ON org_memberships")
    op.execute("DROP POLICY IF EXISTS org_memberships_update ON org_memberships")
    op.execute("DROP POLICY IF EXISTS org_memberships_delete ON org_memberships")
    op.execute(
        """
        CREATE POLICY org_memberships_select ON org_memberships FOR SELECT
        USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
            OR user_id = NULLIF(
                current_setting('app.user_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY org_memberships_insert ON org_memberships FOR INSERT
        WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY org_memberships_update ON org_memberships FOR UPDATE
        USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        WITH CHECK (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )
    op.execute(
        """
        CREATE POLICY org_memberships_delete ON org_memberships FOR DELETE
        USING (
            tenant_id = NULLIF(
                current_setting('app.tenant_id', true), ''
            )::uuid
        )
        """
    )

    # Generic groups replace the department-specific + ltree representation.
    # One parent per group is the single source of truth; recursive queries are
    # bounded by the service layer.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS groups (
            group_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            parent_group_id uuid,
            kind text NOT NULL DEFAULT 'team',
            name text NOT NULL,
            source text NOT NULL DEFAULT 'native',
            status text NOT NULL DEFAULT 'active',
            created_by uuid NOT NULL REFERENCES users(user_id),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_groups_id_tenant UNIQUE(group_id, tenant_id),
            CONSTRAINT uq_groups_parent_name
                UNIQUE(tenant_id, parent_group_id, name),
            CONSTRAINT ck_groups_kind
                CHECK (kind IN ('department','team')),
            CONSTRAINT ck_groups_source
                CHECK (source IN ('native','idp')),
            CONSTRAINT ck_groups_status
                CHECK (status IN ('active','archived')),
            CONSTRAINT fk_groups_parent_same_organization
                FOREIGN KEY(parent_group_id, tenant_id)
                REFERENCES groups(group_id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_groups_tenant_parent "
        "ON groups(tenant_id, parent_group_id, name)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS "
        "uq_groups_active_parent_name "
        "ON groups("
        "tenant_id, "
        "COALESCE(parent_group_id, "
        "'00000000-0000-0000-0000-000000000000'::uuid), "
        "lower(name)"
        ") WHERE status = 'active'"
    )
    for constraint in (
        "ck_groups_kind",
        "ck_groups_source",
        "ck_groups_status",
    ):
        op.execute(f"ALTER TABLE groups DROP CONSTRAINT IF EXISTS {constraint}")
    op.execute(
        "ALTER TABLE groups ADD CONSTRAINT ck_groups_kind "
        "CHECK (kind IN ('department','team'))"
    )
    op.execute(
        "ALTER TABLE groups ADD CONSTRAINT ck_groups_source "
        "CHECK (source IN ('native','idp'))"
    )
    op.execute(
        "ALTER TABLE groups ADD CONSTRAINT ck_groups_status "
        "CHECK (status IN ('active','archived'))"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS group_memberships (
            membership_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            group_id uuid NOT NULL,
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            group_role text NOT NULL DEFAULT 'member',
            status text NOT NULL DEFAULT 'active',
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_group_membership UNIQUE(user_id, group_id),
            CONSTRAINT ck_group_membership_role
                CHECK (group_role IN ('lead','member')),
            CONSTRAINT ck_group_membership_status
                CHECK (status IN ('active','suspended','revoked')),
            CONSTRAINT fk_group_membership_same_organization
                FOREIGN KEY(group_id, tenant_id)
                REFERENCES groups(group_id, tenant_id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_group_memberships_tenant_user "
        "ON group_memberships(tenant_id, user_id, status)"
    )
    op.execute(
        "ALTER TABLE group_memberships "
        "DROP CONSTRAINT IF EXISTS ck_group_membership_role"
    )
    op.execute(
        "ALTER TABLE group_memberships "
        "ADD CONSTRAINT ck_group_membership_role "
        "CHECK (group_role IN ('lead','member'))"
    )
    op.execute(
        "ALTER TABLE group_memberships "
        "DROP CONSTRAINT IF EXISTS ck_group_membership_status"
    )
    op.execute(
        "ALTER TABLE group_memberships "
        "ADD CONSTRAINT ck_group_membership_status "
        "CHECK (status IN ('active','suspended','revoked'))"
    )

    # ``created_by`` did not exist on departments. Seed it from the oldest
    # organization owner; every organization created by 022 has one.
    op.execute(
        """
        INSERT INTO groups (
            group_id, tenant_id, parent_group_id, kind, name, source, status,
            created_by, created_at, updated_at
        )
        SELECT
            department.dept_id,
            department.tenant_id,
            department.parent_dept_id,
            'department',
            department.name,
            'native',
            'active',
            owner.user_id,
            department.created_at,
            department.created_at
        FROM departments AS department
        JOIN LATERAL (
            SELECT membership.user_id
            FROM org_memberships AS membership
            WHERE membership.tenant_id = department.tenant_id
              AND membership.org_role = 'owner'
            ORDER BY membership.created_at, membership.membership_id
            LIMIT 1
        ) AS owner ON true
        ON CONFLICT (group_id) DO NOTHING
        """
    )
    op.execute(
        """
        INSERT INTO group_memberships (
            membership_id, user_id, group_id, tenant_id, group_role, status,
            created_at, updated_at
        )
        SELECT
            membership_id, user_id, dept_id, tenant_id, dept_role, 'active',
            created_at, created_at
        FROM dept_memberships
        ON CONFLICT (user_id, group_id) DO NOTHING
        """
    )

    for table in ("groups", "group_memberships"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} FOR ALL "
            "USING ("
            "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
            ") WITH CHECK ("
            "tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid"
            ")"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO vibecanvas_app"
        )

    # Existing department grants retain their meaning until migration to
    # OpenFGA. The old table names are then removed to avoid dual hierarchy.
    op.execute(
        "ALTER TABLE resource_grants "
        "DROP CONSTRAINT IF EXISTS ck_grant_principal"
    )
    op.execute(
        "UPDATE resource_grants SET principal_type = 'group' "
        "WHERE principal_type = 'department'"
    )
    op.execute(
        """
        ALTER TABLE resource_grants
        ADD CONSTRAINT ck_grant_principal
        CHECK (
            principal_type IN ('user','group','organization','public')
        )
        """
    )
    op.execute("DROP TABLE IF EXISTS dept_memberships")
    op.execute("DROP TABLE IF EXISTS departments")

    # Session identity is independent of the bearer hash and has an explicit
    # revocation generation. Backfill first, then enforce non-null/FKs.
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS session_id uuid DEFAULT gen_random_uuid()"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS active_organization_id uuid"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS generation bigint NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD COLUMN IF NOT EXISTS authentication_strength "
        "text NOT NULL DEFAULT 'password'"
    )
    op.execute(
        "UPDATE sessions SET session_id = gen_random_uuid() "
        "WHERE session_id IS NULL"
    )
    op.execute(
        "UPDATE sessions SET active_organization_id = tenant_id "
        "WHERE active_organization_id IS NULL"
    )
    op.execute("ALTER TABLE sessions ALTER COLUMN session_id SET NOT NULL")
    op.execute(
        "ALTER TABLE sessions "
        "ALTER COLUMN active_organization_id SET NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sessions_session_id "
        "ON sessions(session_id)"
    )
    op.execute(
        "ALTER TABLE sessions "
        "DROP CONSTRAINT IF EXISTS fk_sessions_active_organization"
    )
    op.execute(
        """
        ALTER TABLE sessions
        ADD CONSTRAINT fk_sessions_active_organization
        FOREIGN KEY(active_organization_id)
        REFERENCES organizations(tenant_id)
        """
    )
    for table in ("organizations", "org_memberships", "resource_grants"):
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        "ALTER TABLE sessions "
        "DROP CONSTRAINT IF EXISTS ck_sessions_generation"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD CONSTRAINT ck_sessions_generation CHECK (generation > 0)"
    )
    op.execute(
        "ALTER TABLE sessions "
        "DROP CONSTRAINT IF EXISTS "
        "ck_sessions_active_organization_matches_tenant"
    )
    op.execute(
        "ALTER TABLE sessions "
        "ADD CONSTRAINT ck_sessions_active_organization_matches_tenant "
        "CHECK (tenant_id = active_organization_id)"
    )
    op.execute(
        "ALTER TABLE sessions "
        "DROP CONSTRAINT IF EXISTS ck_sessions_authentication_strength"
    )
    op.execute(
        """
        ALTER TABLE sessions
        ADD CONSTRAINT ck_sessions_authentication_strength
        CHECK (
            authentication_strength IN ('password','oauth','mfa','recovery')
        )
        """
    )


def downgrade() -> None:
    # A downgrade retains new group data by reconstructing the legacy tables.
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS departments (
            dept_id uuid PRIMARY KEY,
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            parent_dept_id uuid
                REFERENCES departments(dept_id) ON DELETE CASCADE,
            path ltree NOT NULL,
            name text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        WITH RECURSIVE tree AS (
            SELECT
                group_id, tenant_id, parent_group_id,
                text2ltree(replace(group_id::text, '-', '_')) AS path,
                name, created_at
            FROM groups
            WHERE parent_group_id IS NULL
            UNION ALL
            SELECT
                child.group_id, child.tenant_id, child.parent_group_id,
                tree.path || text2ltree(replace(child.group_id::text, '-', '_')),
                child.name, child.created_at
            FROM groups AS child
            JOIN tree ON tree.group_id = child.parent_group_id
        )
        INSERT INTO departments(
            dept_id, tenant_id, parent_dept_id, path, name, created_at
        )
        SELECT group_id, tenant_id, parent_group_id, path, name, created_at
        FROM tree
        ON CONFLICT (dept_id) DO NOTHING
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS dept_memberships (
            membership_id uuid PRIMARY KEY,
            user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            dept_id uuid NOT NULL
                REFERENCES departments(dept_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL
                REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            dept_role text NOT NULL DEFAULT 'member',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_dept_membership UNIQUE(user_id, dept_id)
        )
        """
    )
    op.execute(
        """
        INSERT INTO dept_memberships(
            membership_id, user_id, dept_id, tenant_id, dept_role, created_at
        )
        SELECT
            membership_id, user_id, group_id, tenant_id, group_role, created_at
        FROM group_memberships
        ON CONFLICT (user_id, dept_id) DO NOTHING
        """
    )
    op.execute("DROP TABLE IF EXISTS group_memberships")
    op.execute("DROP TABLE IF EXISTS groups")

    op.execute(
        "ALTER TABLE sessions "
        "DROP CONSTRAINT IF EXISTS fk_sessions_active_organization"
    )
    for column in (
        "authentication_strength",
        "generation",
        "active_organization_id",
        "session_id",
    ):
        op.execute(f"ALTER TABLE sessions DROP COLUMN IF EXISTS {column}")

    op.execute(
        "ALTER TABLE org_memberships DROP CONSTRAINT IF EXISTS ck_org_role"
    )
    op.execute(
        """
        ALTER TABLE org_memberships
        ADD CONSTRAINT ck_org_role
        CHECK (org_role IN ('owner','admin','member'))
        """
    )
    for column in ("updated_at", "invited_by", "status"):
        op.execute(
            f"ALTER TABLE org_memberships DROP COLUMN IF EXISTS {column}"
        )
    for column in ("updated_at", "created_by"):
        op.execute(
            f"ALTER TABLE organizations DROP COLUMN IF EXISTS {column}"
        )
