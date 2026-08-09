"""Enforce ciphertext-only deletion email snapshots.

Revision ID: 096
Revises: 095
Create Date: 2026-08-01
"""
from alembic import op


revision = "096"
down_revision = "095"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM account_deletion_requests
             WHERE email_snapshot_key_id IS NULL
                OR email_snapshot_ciphertext IS NULL
                OR email_snapshot_ciphertext = ''
                OR email_snapshot_nonce IS NULL OR email_snapshot_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'account deletion PII migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    op.execute("UPDATE account_deletion_requests SET email_snapshot='' ")
    op.execute(
        "ALTER TABLE account_deletion_requests ADD CONSTRAINT "
        "ck_account_deletion_email_storage CHECK (email_snapshot='' AND (("
        "email_snapshot_key_id IS NOT NULL "
        "AND email_snapshot_ciphertext IS NOT NULL "
        "AND email_snapshot_ciphertext<>'' "
        "AND email_snapshot_nonce IS NOT NULL "
        "AND email_snapshot_nonce<>'') OR ("
        "email_snapshot_key_id IS NULL "
        "AND email_snapshot_ciphertext IS NULL "
        "AND email_snapshot_nonce IS NULL)))"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION scrub_account_deletion_email_plaintext()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.email_snapshot := '';
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_account_deletion_scrub_email "
        "BEFORE INSERT OR UPDATE ON account_deletion_requests FOR EACH ROW "
        "EXECUTE FUNCTION scrub_account_deletion_email_plaintext()"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 096 is intentionally irreversible: deletion email snapshots "
        "must remain ciphertext-only"
    )
