"""account deletion requests — two-phase account erasure.

Revision ID: 029
Revises: 028
Create Date: 2026-07-01
"""
from alembic import op

revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_status")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_status "
        "CHECK (status IN ('active','disabled','pending_deletion'))"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS account_deletion_requests (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id uuid NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
            tenant_id uuid NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
            email_snapshot text NOT NULL,
            status text NOT NULL DEFAULT 'pending',
            requested_at timestamptz NOT NULL DEFAULT now(),
            purge_after timestamptz NOT NULL,
            cancelled_at timestamptz,
            purging_at timestamptz,
            purged_at timestamptz,
            last_error text,
            attempt_count integer NOT NULL DEFAULT 0,
            CONSTRAINT ck_account_deletion_requests_status
              CHECK (status IN ('pending','cancelled','purging','purged','failed'))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_account_deletion_pending_user
          ON account_deletion_requests(user_id)
          WHERE status IN ('pending','purging','failed')
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_account_deletion_due
          ON account_deletion_requests(status, purge_after)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_account_deletion_due")
    op.execute("DROP INDEX IF EXISTS uq_account_deletion_pending_user")
    op.execute("DROP TABLE IF EXISTS account_deletion_requests")
    op.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS ck_users_status")
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_status "
        "CHECK (status IN ('active','disabled'))"
    )
