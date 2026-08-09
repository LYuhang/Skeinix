"""llm_credentials — add optional model context window.

Revision ID: 028
Revises: 027
Create Date: 2026-07-01

Stores the model context window configured with a saved LLM credential. Agent
memory/compression ratios derive concrete token thresholds from this value when
the credential drives a turn; NULL means "use platform default".
"""
from alembic import op

revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE llm_credentials "
        "ADD COLUMN IF NOT EXISTS model_context_tokens integer"
    )
    op.execute(
        "ALTER TABLE llm_credentials "
        "ADD CONSTRAINT llm_credentials_model_context_tokens_positive "
        "CHECK (model_context_tokens IS NULL OR model_context_tokens > 0) "
        "NOT VALID"
    )
    op.execute(
        "ALTER TABLE llm_credentials "
        "VALIDATE CONSTRAINT llm_credentials_model_context_tokens_positive"
    )


def downgrade():
    op.execute(
        "ALTER TABLE llm_credentials "
        "DROP CONSTRAINT IF EXISTS llm_credentials_model_context_tokens_positive"
    )
    op.execute(
        "ALTER TABLE llm_credentials "
        "DROP COLUMN IF EXISTS model_context_tokens"
    )
