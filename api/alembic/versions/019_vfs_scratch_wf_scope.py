"""019 — re-key vfs_scratch from chat_id to wf_id (unify persistent VFS on wf scope).

/memory becomes workflow-scoped (was chat-scoped). Backfill wf_id from chats,
dedupe latest-wins per (wf_id, path), swap the PK. Downgrade is data-lossy for
collapsed rows (documented).

NOTE: migration 001 builds a fresh DB via Base.metadata.create_all against the
CURRENT models (see 001_initial_schema.py), so on a from-scratch upgrade the
vfs_scratch table is ALREADY wf-scoped (no chat_id column) by the time this
migration runs. The chat_id-dependent backfill/swap steps are therefore guarded
to run ONLY when a chat_id column is actually present — i.e. on a real
production DB upgrading in-place from 018. On a fresh DB they no-op cleanly.
This mirrors the idempotent style of migrations 012–018.
"""
from alembic import op

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'vfs_scratch' AND column_name = 'chat_id'
            ) THEN
                -- In-place re-key path (production DB at 018, chat-scoped).
                ALTER TABLE vfs_scratch ADD COLUMN IF NOT EXISTS wf_id TEXT;
                UPDATE vfs_scratch s SET wf_id = c.wf_id FROM chats c
                    WHERE c.chat_id = s.chat_id;
                DELETE FROM vfs_scratch WHERE wf_id IS NULL;
                DELETE FROM vfs_scratch a USING vfs_scratch b
                    WHERE a.wf_id = b.wf_id AND a.path = b.path
                    AND (a.last_access, a.ctid) < (b.last_access, b.ctid);
                ALTER TABLE vfs_scratch ALTER COLUMN wf_id SET NOT NULL;
                DROP INDEX IF EXISTS ix_vfs_scratch_access;
                ALTER TABLE vfs_scratch DROP CONSTRAINT IF EXISTS vfs_scratch_pkey;
                ALTER TABLE vfs_scratch DROP COLUMN IF EXISTS chat_id;
                ALTER TABLE vfs_scratch ADD PRIMARY KEY (wf_id, path);
                ALTER TABLE vfs_scratch ADD CONSTRAINT vfs_scratch_wf_fk
                    FOREIGN KEY (wf_id) REFERENCES workflows(wf_id) ON DELETE CASCADE;
                CREATE INDEX ix_vfs_scratch_access ON vfs_scratch (wf_id, last_access);
            END IF;
            -- Fresh DB (built from current wf-scoped models): nothing to do.
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE vfs_scratch ADD COLUMN IF NOT EXISTS chat_id TEXT")
    op.execute("ALTER TABLE vfs_scratch DROP CONSTRAINT IF EXISTS vfs_scratch_wf_fk")
    op.execute("ALTER TABLE vfs_scratch DROP CONSTRAINT IF EXISTS vfs_scratch_pkey")
    op.execute("DROP INDEX IF EXISTS ix_vfs_scratch_access")
    op.execute("ALTER TABLE vfs_scratch DROP COLUMN IF EXISTS wf_id")
