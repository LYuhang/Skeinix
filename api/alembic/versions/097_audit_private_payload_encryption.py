"""Add migration-only encrypted audit private payload columns.

Revision ID: 097
Revises: 096
Create Date: 2026-08-01
"""
from alembic import op


revision = "097"
down_revision = "096"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS actor_lookup_hash text")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS ip_lookup_hash text")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS private_ciphertext text")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS private_nonce text")
    op.execute("ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS private_key_id uuid")
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.table_schema=kcu.table_schema
           WHERE tc.table_schema=current_schema() AND tc.table_name='audit_log'
             AND tc.constraint_type='FOREIGN KEY'
             AND kcu.column_name='private_key_id'
          ) THEN
            ALTER TABLE audit_log ADD CONSTRAINT fk_audit_private_key
              FOREIGN KEY (private_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )
    # Deployment is paused at this revision. The only writer is the strict
    # migrator; revision 098 restores append-only enforcement immediately.
    op.execute("DROP TRIGGER IF EXISTS trg_audit_append_only ON audit_log")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE "
        "rolname='vibecanvas_migrator') THEN GRANT SELECT, UPDATE "
        "ON audit_log TO vibecanvas_migrator; END IF; END $$"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 097 is part of an intentionally irreversible audit cutover"
    )
