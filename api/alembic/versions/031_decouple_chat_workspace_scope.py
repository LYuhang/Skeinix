"""Decouple chat/VFS workspace scopes from workflow rows.

Revision ID: 031
Revises: 030
Create Date: 2026-07-02
"""
from alembic import op


revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def _drop_fks_to_workflows(table: str) -> None:
    op.execute(
        f"""
        DO $$
        DECLARE
            r record;
        BEGIN
            FOR r IN
                SELECT conname
                FROM pg_constraint
                WHERE conrelid = '{table}'::regclass
                  AND contype = 'f'
                  AND confrelid = 'workflows'::regclass
            LOOP
                EXECUTE format('ALTER TABLE {table} DROP CONSTRAINT %I', r.conname);
            END LOOP;
        END $$;
        """
    )


def _rename_column_if_exists(table: str, old: str, new: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = '{table}'
                  AND column_name = '{old}'
            ) AND NOT EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = '{table}'
                  AND column_name = '{new}'
            ) THEN
                ALTER TABLE {table} RENAME COLUMN {old} TO {new};
            END IF;
        END $$;
        """
    )


def upgrade() -> None:
    for table in ("chats", "vfs_artifacts", "vfs_scratch"):
        _drop_fks_to_workflows(table)

    op.execute("DROP INDEX IF EXISTS ix_chats_wf_last_msg")
    op.execute("DROP INDEX IF EXISTS ix_chats_surface_wf_last_msg")
    op.execute("DROP INDEX IF EXISTS ix_vfs_artifacts_access")
    op.execute("DROP INDEX IF EXISTS ix_vfs_scratch_access")

    _rename_column_if_exists("chats", "wf_id", "scope_id")
    _rename_column_if_exists("vfs_artifacts", "wf_id", "scope_id")
    _rename_column_if_exists("vfs_scratch", "wf_id", "scope_id")

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chats_scope_last_msg "
        "ON chats (scope_id, last_message_at) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chats_surface_scope_last_msg "
        "ON chats (surface, scope_id, last_message_at) WHERE deleted_at IS NULL"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vfs_artifacts_access "
        "ON vfs_artifacts (scope_id, last_access)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_vfs_scratch_access "
        "ON vfs_scratch (scope_id, last_access)"
    )

    # Remove legacy hidden carrier/workspace rows. Their dependent chat/VFS rows
    # remain valid because the foreign keys above have been removed; real user
    # workflows are untouched.
    op.execute(
        """
        DELETE FROM workflow_versions
        WHERE wf_id IN (
            SELECT wf_id FROM workflows
            WHERE domain IN ('system_chat', 'system_chat_workspace', 'system_browser_chat')
        )
        """
    )
    op.execute(
        """
        DELETE FROM workflows
        WHERE domain IN ('system_chat', 'system_chat_workspace', 'system_browser_chat')
        """
    )


def downgrade() -> None:
    # Irreversible without re-creating one hidden workflow row per existing chat/VFS
    # scope. Keep downgrade as a no-op rather than manufacturing user-visible
    # workflow rows again.
    pass
