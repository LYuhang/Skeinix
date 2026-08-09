"""Assign every saved LLM credential to exactly one Agent Runtime.

Revision ID: 117
Revises: 116
Create Date: 2026-08-09
"""
from alembic import op

revision = "117"
down_revision = "116"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Credentials created before this distinction belonged to the general API
    # Management surface used by LangChain, so preserve that behavior without
    # exposing them in Codex's catalog.
    op.execute(
        "ALTER TABLE llm_credentials "
        "ADD COLUMN IF NOT EXISTS runtime_scope text NOT NULL DEFAULT 'langchain'"
    )
    op.execute(
        "DO $$ BEGIN "
        "IF NOT EXISTS ("
        "SELECT 1 FROM pg_constraint "
        "WHERE conname = 'ck_llm_credentials_runtime_scope' "
        "AND conrelid = 'llm_credentials'::regclass"
        ") THEN "
        "ALTER TABLE llm_credentials ADD CONSTRAINT "
        "ck_llm_credentials_runtime_scope "
        "CHECK (runtime_scope IN ('langchain', 'codex')); "
        "END IF; END $$"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE llm_credentials DROP CONSTRAINT IF EXISTS "
        "ck_llm_credentials_runtime_scope"
    )
    op.execute("ALTER TABLE llm_credentials DROP COLUMN IF EXISTS runtime_scope")
