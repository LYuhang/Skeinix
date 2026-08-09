"""Remove plaintext Chat, Workflow, version-note, and schedule-name metadata.

Revision ID: 085
Revises: 084
Create Date: 2026-07-31
"""
from alembic import op


revision = "085"
down_revision = "084"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM chats
             WHERE metadata_key_id IS NULL
                OR metadata_ciphertext = '' OR metadata_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'Chat metadata migration incomplete; run strict content migrator';
          END IF;
          IF EXISTS (
            SELECT 1 FROM workflows
             WHERE metadata_key_id IS NULL
                OR metadata_ciphertext = '' OR metadata_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'Workflow metadata migration incomplete; run strict content migrator';
          END IF;
          IF EXISTS (
            SELECT 1 FROM workflow_versions
             WHERE note_key_id IS NULL
                OR note_ciphertext = '' OR note_nonce = ''
          ) THEN
            RAISE EXCEPTION
              'Workflow note migration incomplete; run strict content migrator';
          END IF;
          IF EXISTS (
            SELECT 1 FROM task_schedules
             WHERE private_schema_version IS DISTINCT FROM 2
          ) THEN
            RAISE EXCEPTION
              'Task schedule metadata migration incomplete; run strict content migrator';
          END IF;
        END $$
        """
    )
    # There is no structural/plaintext compatibility row shape after cutover:
    # every application row carries a complete authenticated envelope.
    for table in ("chats", "workflows"):
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN metadata_key_id SET NOT NULL"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN metadata_ciphertext DROP DEFAULT"
        )
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN metadata_nonce DROP DEFAULT"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_metadata_envelope "
            "CHECK (metadata_ciphertext <> '' AND metadata_nonce <> '')"
        )
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN note_key_id SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN note_ciphertext DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE workflow_versions ALTER COLUMN note_nonce DROP DEFAULT"
    )
    op.execute(
        "ALTER TABLE workflow_versions ADD CONSTRAINT "
        "ck_workflow_versions_note_envelope CHECK (note_ciphertext <> '' "
        "AND note_nonce <> '')"
    )
    op.execute(
        "ALTER TABLE task_schedules ALTER COLUMN private_schema_version "
        "SET DEFAULT 2"
    )
    op.execute(
        "ALTER TABLE task_schedules ALTER COLUMN private_schema_version "
        "SET NOT NULL"
    )
    op.execute(
        "ALTER TABLE task_schedules ADD CONSTRAINT "
        "ck_task_schedules_private_schema CHECK (private_schema_version = 2)"
    )
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS name")
    op.execute("ALTER TABLE chats DROP COLUMN IF EXISTS meta")
    op.execute("ALTER TABLE workflows DROP COLUMN IF EXISTS workflow_name")
    op.execute("ALTER TABLE workflows DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE workflows DROP COLUMN IF EXISTS tags")
    op.execute("ALTER TABLE workflow_versions DROP COLUMN IF EXISTS note")
    op.execute("ALTER TABLE task_schedules DROP COLUMN IF EXISTS name")


def downgrade() -> None:
    raise RuntimeError(
        "revision 085 is intentionally irreversible: plaintext metadata "
        "columns are not part of the current storage model"
    )
