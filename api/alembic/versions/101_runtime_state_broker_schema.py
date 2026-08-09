"""Provision host-owned Runtime State Broker tables.

Revision ID: 101
Revises: 100
Create Date: 2026-08-01
"""
from alembic import op


revision = "101"
down_revision = "100"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vc_runtime_checkpoints (
            organization_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            runtime_session_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            parent_checkpoint_id TEXT NULL,
            checkpoint_serialization TEXT NOT NULL,
            checkpoint_payload BYTEA NOT NULL,
            metadata_serialization TEXT NOT NULL,
            metadata_payload BYTEA NOT NULL,
            metadata_index JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (
                organization_id, chat_id, runtime_session_id, thread_id,
                checkpoint_ns, checkpoint_id
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_vc_runtime_checkpoints_latest
        ON vc_runtime_checkpoints (
            organization_id, chat_id, runtime_session_id, thread_id,
            checkpoint_ns, checkpoint_id DESC
        )
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vc_runtime_checkpoint_writes (
            organization_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            runtime_session_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id TEXT NOT NULL,
            task_path TEXT NOT NULL DEFAULT '',
            write_index INTEGER NOT NULL,
            channel TEXT NOT NULL,
            value_serialization TEXT NOT NULL,
            value_payload BYTEA NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (
                organization_id, chat_id, runtime_session_id, thread_id,
                checkpoint_ns, checkpoint_id, task_id, task_path, write_index
            )
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_vc_runtime_checkpoint_writes_lookup
        ON vc_runtime_checkpoint_writes (
            organization_id, chat_id, runtime_session_id, thread_id,
            checkpoint_ns, checkpoint_id, task_id, write_index
        )
        """
    )


def downgrade() -> None:
    op.drop_table("vc_runtime_checkpoint_writes")
    op.drop_table("vc_runtime_checkpoints")
