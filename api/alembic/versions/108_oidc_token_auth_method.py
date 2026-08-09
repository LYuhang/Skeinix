"""Add standards-based OIDC token endpoint client authentication mode.

Revision ID: 108
Revises: 107
Create Date: 2026-08-02
"""

from alembic import op


revision = "108"
down_revision = "107"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE enterprise_identity_providers ADD COLUMN IF NOT EXISTS "
        "token_endpoint_auth_method TEXT NOT NULL DEFAULT 'client_secret_basic'"
    )
    op.execute(
        "ALTER TABLE enterprise_identity_providers DROP CONSTRAINT IF EXISTS "
        "ck_enterprise_identity_provider_token_auth_method"
    )
    op.execute(
        "ALTER TABLE enterprise_identity_providers ADD CONSTRAINT "
        "ck_enterprise_identity_provider_token_auth_method CHECK "
        "(token_endpoint_auth_method IN "
        "('client_secret_basic','client_secret_post','none'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE enterprise_identity_providers DROP CONSTRAINT IF EXISTS "
        "ck_enterprise_identity_provider_token_auth_method"
    )
    op.execute(
        "ALTER TABLE enterprise_identity_providers DROP COLUMN IF EXISTS "
        "token_endpoint_auth_method"
    )
