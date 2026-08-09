"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-05-16
"""
from alembic import op
from vibecanvas_api.storage.models import (
    Base, UPDATED_AT_TRIGGER_FN, UPDATED_AT_TRIGGERS,
)

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    op.execute(UPDATED_AT_TRIGGER_FN)
    for table, trg in UPDATED_AT_TRIGGERS:
        op.execute(
            f"CREATE TRIGGER {trg} BEFORE UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        )


def downgrade():
    bind = op.get_bind()
    for table, trg in UPDATED_AT_TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {trg} ON {table};")
    op.execute("DROP FUNCTION IF EXISTS set_updated_at();")
    Base.metadata.drop_all(bind)
