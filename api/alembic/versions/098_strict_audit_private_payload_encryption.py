"""Enforce encrypted/minimized audit private payload storage.

Revision ID: 098
Revises: 097
Create Date: 2026-08-01
"""
from alembic import op


revision = "098"
down_revision = "097"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM audit_log WHERE tenant_id IS NOT NULL AND (
                 private_key_id IS NULL OR private_ciphertext IS NULL
              OR private_ciphertext='' OR private_nonce IS NULL
              OR private_nonce='')
          ) OR EXISTS (
            SELECT 1 FROM audit_log WHERE actor_email IS NOT NULL
               OR target_name IS NOT NULL OR ip_address IS NOT NULL
               OR user_agent IS NOT NULL OR meta<>'{}'::jsonb
          ) THEN
            RAISE EXCEPTION
              'audit private migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE audit_log ADD CONSTRAINT ck_audit_private_storage CHECK ("
        "actor_email IS NULL AND target_name IS NULL AND ip_address IS NULL "
        "AND user_agent IS NULL AND meta='{}'::jsonb AND (("
        "tenant_id IS NOT NULL AND private_key_id IS NOT NULL "
        "AND private_ciphertext IS NOT NULL AND private_ciphertext<>'' "
        "AND private_nonce IS NOT NULL AND private_nonce<>'') OR ("
        "private_key_id IS NULL AND private_ciphertext IS NULL "
        "AND private_nonce IS NULL)))"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION scrub_audit_private_plaintext()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          NEW.actor_email := NULL;
          NEW.target_name := NULL;
          NEW.ip_address := NULL;
          NEW.user_agent := NULL;
          NEW.meta := '{}'::jsonb;
          RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_audit_scrub_private_plaintext "
        "BEFORE INSERT ON audit_log FOR EACH ROW "
        "EXECUTE FUNCTION scrub_audit_private_plaintext()"
    )
    op.execute(
        "CREATE TRIGGER trg_audit_append_only "
        "BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW "
        "EXECUTE FUNCTION audit_log_append_only()"
    )
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM vibecanvas_app")
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM pg_roles
             WHERE rolname='vibecanvas_maintenance'
          ) THEN
            REVOKE UPDATE, DELETE ON audit_log
              FROM vibecanvas_maintenance;
          END IF;
        END $$
        """
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE "
        "rolname='vibecanvas_migrator') THEN REVOKE UPDATE, DELETE "
        "ON audit_log FROM vibecanvas_migrator; END IF; END $$"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 098 is intentionally irreversible: audit private fields must "
        "remain encrypted or minimized"
    )
