"""020 — drop the unused `content` column from vfs_artifacts/vfs_scratch.

Post-unification every VFS write is object-backed (bytes in the ObjectStore at
object_key); Postgres is a pure metadata index. The `content` column only held
data for PRE-unification (legacy) rows, which the one-shot backfill script
(`scripts/backfill_vfs_content.py`) moves to the ObjectStore.

DEPLOY ORDER: run `scripts/backfill_vfs_content.py` BEFORE this migration — it
drops the legacy data otherwise. In a fresh/dev/CI DB there are no legacy rows
and `create_all` builds from the post-drop model (the column never exists), so
the DROP is an idempotent no-op.
"""
from alembic import op

revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE vfs_artifacts DROP COLUMN IF EXISTS content")
    op.execute("ALTER TABLE vfs_scratch   DROP COLUMN IF EXISTS content")


def downgrade() -> None:
    # NOT NULL DEFAULT '' so the re-add succeeds on existing rows (a bare
    # NOT NULL add would fail). The legacy data is NOT restored.
    op.execute(
        "ALTER TABLE vfs_artifacts ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''")
    op.execute(
        "ALTER TABLE vfs_scratch   ADD COLUMN IF NOT EXISTS content TEXT NOT NULL DEFAULT ''")
