"""Add durable Service Accounts for non-interactive execution roots.

Revision ID: 065
Revises: 064
Create Date: 2026-07-31
"""

from alembic import op


revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS service_accounts (
            service_account_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            name text NOT NULL,
            kind text NOT NULL,
            owner_resource_type text NOT NULL,
            owner_resource_id text NOT NULL,
            status text NOT NULL DEFAULT 'active',
            generation integer NOT NULL DEFAULT 1,
            created_by uuid NOT NULL REFERENCES users(user_id) ON DELETE RESTRICT,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            disabled_at timestamptz,
            CONSTRAINT ck_service_accounts_kind
                CHECK (kind IN ('deployment', 'schedule', 'task', 'integration')),
            CONSTRAINT ck_service_accounts_owner_resource_type
                CHECK (owner_resource_type IN ('deployment', 'task', 'integration')),
            CONSTRAINT ck_service_accounts_status
                CHECK (status IN ('active', 'disabled', 'deleted')),
            CONSTRAINT ck_service_accounts_generation CHECK (generation > 0),
            CONSTRAINT uq_service_accounts_owner
                UNIQUE (tenant_id, owner_resource_type, owner_resource_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS service_account_credentials (
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            service_account_id uuid NOT NULL
                REFERENCES service_accounts(service_account_id) ON DELETE CASCADE,
            credential_id uuid NOT NULL
                REFERENCES llm_credentials(id) ON DELETE CASCADE,
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (service_account_id, credential_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_service_accounts_tenant_status "
        "ON service_accounts (tenant_id, status, updated_at)"
    )
    op.execute(
        "ALTER TABLE tasks ADD COLUMN IF NOT EXISTS service_account_id uuid "
        "REFERENCES service_accounts(service_account_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE task_schedules ADD COLUMN IF NOT EXISTS service_account_id uuid "
        "REFERENCES service_accounts(service_account_id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS service_account_id uuid "
        "REFERENCES service_accounts(service_account_id) ON DELETE RESTRICT"
    )

    # Existing rows receive one stable identity per execution root. Nullable
    # association columns remain intentional: a legacy/corrupt row must fail
    # closed at worker pickup instead of making this online migration unsafe.
    op.execute(
        """
        INSERT INTO service_accounts (
            tenant_id, name, kind, owner_resource_type, owner_resource_id,
            created_by
        )
        SELECT t.tenant_id,
               CASE WHEN t.task_type = 'scheduled_run'
                    THEN 'Scheduled task ' ELSE 'Batch task ' END || t.id::text,
               CASE WHEN t.task_type = 'scheduled_run'
                    THEN 'schedule' ELSE 'task' END,
               'task', t.id::text, t.user_id
        FROM tasks AS t
        ON CONFLICT (tenant_id, owner_resource_type, owner_resource_id)
        DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE tasks AS t
        SET service_account_id = sa.service_account_id
        FROM service_accounts AS sa
        WHERE sa.tenant_id = t.tenant_id
          AND sa.owner_resource_type = 'task'
          AND sa.owner_resource_id = t.id::text
          AND t.service_account_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE task_schedules AS s
        SET service_account_id = t.service_account_id
        FROM tasks AS t
        WHERE t.id = s.task_id
          AND s.service_account_id IS NULL
        """
    )
    op.execute(
        """
        INSERT INTO service_accounts (
            tenant_id, name, kind, owner_resource_type, owner_resource_id,
            created_by
        )
        SELECT d.tenant_id, 'Deployment ' || d.id::text, 'deployment',
               'deployment', d.id::text, d.user_id
        FROM deployments AS d
        ON CONFLICT (tenant_id, owner_resource_type, owner_resource_id)
        DO NOTHING
        """
    )
    op.execute(
        """
        UPDATE deployments AS d
        SET service_account_id = sa.service_account_id
        FROM service_accounts AS sa
        WHERE sa.tenant_id = d.tenant_id
          AND sa.owner_resource_type = 'deployment'
          AND sa.owner_resource_id = d.id::text
          AND d.service_account_id IS NULL
        """
    )

    op.execute("ALTER TABLE service_accounts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE service_accounts FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON service_accounts")
    op.execute(
        "CREATE POLICY tenant_isolation ON service_accounts "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON service_accounts TO vibecanvas_app"
    )
    op.execute("ALTER TABLE service_account_credentials ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE service_account_credentials FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON service_account_credentials")
    op.execute(
        "CREATE POLICY tenant_isolation ON service_account_credentials "
        "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
        "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON service_account_credentials "
        "TO vibecanvas_app"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS service_account_id")
    op.execute("ALTER TABLE task_schedules DROP COLUMN IF EXISTS service_account_id")
    op.execute("ALTER TABLE tasks DROP COLUMN IF EXISTS service_account_id")
    op.execute("DROP TABLE IF EXISTS service_account_credentials")
    op.execute("DROP TABLE IF EXISTS service_accounts")
