"""018 — add nullable object_key to vfs_artifacts/vfs_scratch (persistent VFS binary).

Binary persistent files store bytes in the ObjectStore at object_key; legacy/text
rows keep object_key NULL and read from the content column. No backfill.
"""
from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE vfs_artifacts ADD COLUMN IF NOT EXISTS object_key TEXT")
    op.execute("ALTER TABLE vfs_scratch  ADD COLUMN IF NOT EXISTS object_key TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE vfs_artifacts DROP COLUMN IF EXISTS object_key")
    op.execute("ALTER TABLE vfs_scratch  DROP COLUMN IF EXISTS object_key")
