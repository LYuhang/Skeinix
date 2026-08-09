"""Add migration-only envelope columns for identity PII.

Revision ID: 093
Revises: 092
Create Date: 2026-08-01
"""
from alembic import op


revision = "093"
down_revision = "092"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_ciphertext text")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_nonce text")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS profile_key_id uuid")
    op.execute(
        "ALTER TABLE auth_identities ADD COLUMN IF NOT EXISTS tenant_id uuid"
    )
    op.execute(
        "UPDATE auth_identities i SET tenant_id=u.tenant_id FROM users u "
        "WHERE u.user_id=i.user_id AND i.tenant_id IS NULL"
    )
    op.execute(
        "ALTER TABLE auth_identities ADD COLUMN IF NOT EXISTS "
        "provider_uid_lookup_hash text"
    )
    op.execute(
        "ALTER TABLE auth_identities ADD COLUMN IF NOT EXISTS "
        "provider_uid_ciphertext text"
    )
    op.execute(
        "ALTER TABLE auth_identities ADD COLUMN IF NOT EXISTS provider_uid_nonce text"
    )
    op.execute(
        "ALTER TABLE auth_identities ADD COLUMN IF NOT EXISTS provider_uid_key_id uuid"
    )
    # Migration 001 uses current metadata for fresh databases. Explicitly
    # restore the nullable staging contract when those latest columns already
    # exist, then revision 094 makes them strict after backfill.
    op.execute(
        "ALTER TABLE auth_identities ALTER COLUMN tenant_id DROP NOT NULL, "
        "ALTER COLUMN provider_uid_lookup_hash DROP NOT NULL, "
        "ALTER COLUMN provider_uid_ciphertext DROP NOT NULL, "
        "ALTER COLUMN provider_uid_nonce DROP NOT NULL, "
        "ALTER COLUMN provider_uid_key_id DROP NOT NULL"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.table_schema=kcu.table_schema
           WHERE tc.table_schema=current_schema() AND tc.table_name='users'
             AND tc.constraint_type='FOREIGN KEY'
             AND kcu.column_name='profile_key_id'
          ) THEN
            ALTER TABLE users ADD CONSTRAINT fk_users_profile_key
              FOREIGN KEY (profile_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.table_schema=kcu.table_schema
           WHERE tc.table_schema=current_schema()
             AND tc.table_name='auth_identities'
             AND tc.constraint_type='FOREIGN KEY'
             AND kcu.column_name='tenant_id'
          ) THEN
            ALTER TABLE auth_identities ADD CONSTRAINT fk_identities_tenant
              FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id)
              ON DELETE CASCADE;
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name=kcu.constraint_name
             AND tc.table_schema=kcu.table_schema
           WHERE tc.table_schema=current_schema()
             AND tc.table_name='auth_identities'
             AND tc.constraint_type='FOREIGN KEY'
             AND kcu.column_name='provider_uid_key_id'
          ) THEN
            ALTER TABLE auth_identities
              ADD CONSTRAINT fk_identities_provider_uid_key
              FOREIGN KEY (provider_uid_key_id)
              REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT;
          END IF;
        END $$
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 093 is part of an intentionally irreversible identity PII cutover"
    )
