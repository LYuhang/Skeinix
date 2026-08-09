"""llm_credentials — add optional ``proxy`` column.

Revision ID: 023
Revises: 022
Create Date: 2026-06-11

Adds an OPTIONAL per-credential HTTP/HTTPS proxy so OpenAI-compatible calls
(PromptNode engine + chat Agent) can be routed through a user-specified proxy.

Self-contained (does NOT rely on 001's create_all): ``ADD COLUMN IF NOT EXISTS``
is pure DDL — no row write — so even though ``llm_credentials`` is FORCE RLS, no
RLS toggle is needed. Idempotent on every DB shape (fresh or pre-existing).
"""
from alembic import op

revision = "023"
down_revision = "022"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE llm_credentials ADD COLUMN IF NOT EXISTS proxy text"
    )


def downgrade():
    op.execute("ALTER TABLE llm_credentials DROP COLUMN IF EXISTS proxy")
