"""phase 5 auth tables

Revision ID: 002
Revises: 001
"""
from alembic import op
from vibecanvas_api.storage.models import Base

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None

_AUTH_TABLES = ["tenants", "users", "auth_identities", "sessions",
                "password_reset_tokens"]


def upgrade():
    bind = op.get_bind()
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")  # gen_random_uuid
    tables = [Base.metadata.tables[t] for t in _AUTH_TABLES]
    Base.metadata.create_all(bind, tables=tables)
    op.execute(
        "CREATE TRIGGER trg_users_updated_at BEFORE UPDATE ON users "
        "FOR EACH ROW EXECUTE FUNCTION set_updated_at();")


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS trg_users_updated_at ON users;")
    bind = op.get_bind()
    tables = [Base.metadata.tables[t] for t in reversed(_AUTH_TABLES)]
    Base.metadata.drop_all(bind, tables=tables)
