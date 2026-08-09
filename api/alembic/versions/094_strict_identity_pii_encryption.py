"""Enforce ciphertext-only identity PII storage.

Revision ID: 094
Revises: 093
Create Date: 2026-08-01
"""
from alembic import op


revision = "094"
down_revision = "093"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM users
             WHERE profile_key_id IS NULL OR profile_ciphertext IS NULL
                OR profile_ciphertext = '' OR profile_nonce IS NULL
                OR profile_nonce = ''
          ) OR EXISTS (
            SELECT 1 FROM auth_identities
             WHERE tenant_id IS NULL OR provider_uid_lookup_hash IS NULL
                OR provider_uid_lookup_hash = ''
                OR provider_uid_key_id IS NULL
                OR provider_uid_ciphertext IS NULL
                OR provider_uid_ciphertext = ''
                OR provider_uid_nonce IS NULL OR provider_uid_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'identity PII migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    op.execute(
        "UPDATE users SET email='redacted-' || user_id::text || "
        "'@invalid.local', display_name=''"
    )
    op.execute(
        "UPDATE auth_identities SET provider_uid='redacted-' || identity_id::text"
    )
    op.execute(
        "ALTER TABLE auth_identities DROP CONSTRAINT IF EXISTS uq_identity_provider"
    )
    op.execute(
        "ALTER TABLE auth_identities ALTER COLUMN tenant_id SET NOT NULL, "
        "ALTER COLUMN provider_uid_lookup_hash SET NOT NULL, "
        "ALTER COLUMN provider_uid_ciphertext SET NOT NULL, "
        "ALTER COLUMN provider_uid_nonce SET NOT NULL, "
        "ALTER COLUMN provider_uid_key_id SET NOT NULL"
    )
    op.execute(
        """
        DO $$ BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM pg_constraint
             WHERE conrelid='auth_identities'::regclass
               AND conname='uq_identity_provider_lookup'
          ) THEN
            ALTER TABLE auth_identities
              ADD CONSTRAINT uq_identity_provider_lookup
              UNIQUE (provider, provider_uid_lookup_hash);
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE users ADD CONSTRAINT ck_users_identity_pii_storage CHECK ("
        "email = 'redacted-' || user_id::text || '@invalid.local' "
        "AND display_name = '' AND ((profile_key_id IS NOT NULL "
        "AND profile_ciphertext IS NOT NULL AND profile_ciphertext <> '' "
        "AND profile_nonce IS NOT NULL AND profile_nonce <> '') OR ("
        "profile_key_id IS NULL AND profile_ciphertext IS NULL "
        "AND profile_nonce IS NULL)))"
    )
    op.execute(
        "ALTER TABLE auth_identities ADD CONSTRAINT "
        "ck_identities_provider_uid_storage CHECK ("
        "provider_uid = 'redacted-' || identity_id::text "
        "AND provider_uid_lookup_hash <> '' "
        "AND provider_uid_ciphertext <> '' AND provider_uid_nonce <> '')"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION scrub_user_identity_plaintext()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.email := 'redacted-' || NEW.user_id::text || '@invalid.local';
          NEW.display_name := '';
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_users_scrub_identity_plaintext "
        "BEFORE INSERT OR UPDATE ON users FOR EACH ROW "
        "EXECUTE FUNCTION scrub_user_identity_plaintext()"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION scrub_auth_identity_plaintext()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.provider_uid := 'redacted-' || NEW.identity_id::text;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_auth_identities_scrub_plaintext "
        "BEFORE INSERT OR UPDATE ON auth_identities FOR EACH ROW "
        "EXECUTE FUNCTION scrub_auth_identity_plaintext()"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 094 is intentionally irreversible: identity PII must remain "
        "ciphertext-only"
    )
