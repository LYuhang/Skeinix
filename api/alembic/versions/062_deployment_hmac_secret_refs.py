"""Move deployment webhook HMAC material behind SecretService.

Revision ID: 062
Revises: 061
Create Date: 2026-07-31
"""

from alembic import op


revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS "
        "hmac_secret_ref uuid REFERENCES encrypted_secrets(secret_id) "
        "ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE deployments ADD COLUMN IF NOT EXISTS "
        "hmac_secret_version integer NOT NULL DEFAULT 1"
    )
    op.execute(
        "ALTER TABLE deployments DROP CONSTRAINT IF EXISTS "
        "ck_deployments_hmac_required"
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema=current_schema() AND table_name='deployments' "
        "AND column_name='hmac_secret') THEN "
        "ALTER TABLE deployments ADD CONSTRAINT ck_deployments_hmac_required "
        "CHECK ((trigger_type != 'webhook') OR "
        "(hmac_secret_ref IS NOT NULL OR hmac_secret IS NOT NULL)); "
        "ELSE ALTER TABLE deployments ADD CONSTRAINT "
        "ck_deployments_hmac_required CHECK ((trigger_type != 'webhook') OR "
        "(hmac_secret_ref IS NOT NULL)); END IF; END $$"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 062 is intentionally irreversible: webhook HMAC secrets "
        "cannot be restored to plaintext columns"
    )
