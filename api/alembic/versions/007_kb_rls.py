"""kb-rag — 3 tables and FORCE RLS.

Revision ID: 007
Revises: 006
Create Date: 2026-05-27

The three KB tables (``knowledge_bases``, ``kb_files``, ``kb_chunks``)
are created by migration 001's ``Base.metadata.create_all(bind)``, which
reflects the current models (``KnowledgeBase`` / ``KbFile`` / ``KbChunk``
register via ``storage/models.py``'s tail-import of ``models_kb``). This
migration adds what ``create_all`` cannot express:

  - Historical schemas receive HNSW/GIN indexes while their plaintext vector
    and metadata columns still exist. Fresh ciphertext-only schemas skip them.
  - Partial functional index on ``tasks((payload->>'file_id'))`` for the
    ``kb_orphan_reconciler`` lookup path.
  - ENABLE + FORCE ROW LEVEL SECURITY on all 3 tables.
  - ``tenant_isolation`` policy on each (same shape as deployments 005 /
    mcp 006).
  - ``GRANT SELECT, INSERT, UPDATE, DELETE`` on each table to the
    ``vibecanvas_app`` role — mirrors migration 006's convention (004
    / 005 omit GRANT because the owner already has DML implicitly;
    006 / 007 include it explicitly so role-scope review stays uniform
    across the RLS-protected tables).
"""
from alembic import op

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None

TABLES = ("knowledge_bases", "kb_files", "kb_chunks")


def upgrade():
    # These indexes belong only to the pre-encryption schema. Migration 001
    # reflects today's ciphertext-only ORM on a fresh install, so guard both
    # old columns rather than re-introducing plaintext for migration history.
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema=current_schema()
               AND table_name='kb_chunks' AND column_name='embedding'
          ) THEN
            CREATE INDEX IF NOT EXISTS idx_kb_chunks_vector ON kb_chunks
              USING hnsw (embedding vector_cosine_ops)
              WITH (m = 16, ef_construction = 64);
          END IF;
        END $$
        """
    )
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema=current_schema()
               AND table_name='kb_chunks' AND column_name='chunk_metadata'
          ) THEN
            CREATE INDEX IF NOT EXISTS idx_kb_chunks_metadata_gin ON kb_chunks
              USING GIN (chunk_metadata jsonb_path_ops);
          END IF;
        END $$
        """
    )
    # Partial functional index on tasks.payload->>'file_id' for
    # kb_orphan_reconciler (T6 / T11).
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM information_schema.columns
             WHERE table_schema=current_schema()
               AND table_name='tasks' AND column_name='payload'
          ) THEN
            CREATE INDEX IF NOT EXISTS idx_tasks_kb_file_id
              ON tasks ((payload->>'file_id'))
              WHERE task_type = 'kb_index_file';
          END IF;
        END $$
        """
    )

    # RLS — ENABLE + FORCE + policy on each table (Phase 5 / 006 pattern).
    for t in TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} FOR ALL "
            "USING (tenant_id = current_setting('app.tenant_id', true)::uuid) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)"
        )
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO vibecanvas_app"
        )

    # FK on tenant_id -> tenants(tenant_id) ON DELETE CASCADE. The ORM
    # carries this FK in ``models_kb`` so fresh ``create_all`` DBs already
    # have it; this block exists for DBs migrated before the model was
    # updated. DROP IF EXISTS keeps the migration idempotent / re-runnable.
    for t in TABLES:
        op.execute(
            f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS fk_{t}_tenant_id"
        )
        op.execute(
            f"ALTER TABLE {t} ADD CONSTRAINT fk_{t}_tenant_id "
            f"FOREIGN KEY (tenant_id) REFERENCES tenants(tenant_id) ON DELETE CASCADE"
        )


def downgrade():
    for t in TABLES:
        op.execute(
            f"ALTER TABLE {t} DROP CONSTRAINT IF EXISTS fk_{t}_tenant_id"
        )
    for t in TABLES:
        op.execute(
            f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {t} FROM vibecanvas_app"
        )
    for t in TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS idx_tasks_kb_file_id")
    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_metadata_gin")
    op.execute("DROP INDEX IF EXISTS idx_kb_chunks_vector")
    # Extension intentionally not dropped - may be reused by other DBs /
    # future revisions, and DROP EXTENSION cascades to any column using
    # the ``vector`` type (i.e. ``kb_chunks.embedding``), which would
    # silently destroy data on a partial-rollback path.
