"""vfs_run — add nullable ``wf_id`` + a (tenant_id, wf_id) index.

Revision ID: 024
Revises: 023
Create Date: 2026-06-13

"Keep latest run per workflow" lifecycle (UX-10e0): every run now persists its
``/run`` outputs at the end, and a NEW run for the SAME workflow purges the
PREVIOUS run's ``/run`` rows+blobs. The purge query needs to find a workflow's
run rows cheaply, so ``vfs_run`` gains a ``wf_id`` column (matching the Text wf-id
type used by ``vfs_artifacts.wf_id``) and an index on ``(tenant_id, wf_id)``.

``wf_id`` is NULLABLE: existing rows (and any /run-only run with no wf) keep
NULL — they simply won't be matched by the per-workflow purge (back-compat).

Self-contained (does NOT rely on 001's create_all): ``ADD COLUMN IF NOT EXISTS``
is pure DDL — no row write — so even though ``vfs_run`` is FORCE RLS, no RLS
toggle is needed for an added column. Idempotent on every DB shape.
"""
from alembic import op

revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE vfs_run ADD COLUMN IF NOT EXISTS wf_id text")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vfs_run_wf "
        "ON vfs_run (tenant_id, wf_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_vfs_run_wf")
    op.execute("ALTER TABLE vfs_run DROP COLUMN IF EXISTS wf_id")
