"""Remove plaintext HITL and Interactive Artifact content.

Revision ID: 079
Revises: 078
Create Date: 2026-07-31
"""
from alembic import op


revision = "079"
down_revision = "078"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table, label in (
        ("hitl_requests", "HITL"),
        ("interactive_artifacts", "Interactive Artifact"),
    ):
        op.execute(
            f"DO $$ BEGIN IF EXISTS (SELECT 1 FROM {table} WHERE "
            "private_ciphertext IS NULL OR private_nonce IS NULL OR "
            f"private_key_id IS NULL) THEN RAISE EXCEPTION '{label} content "
            "encryption migration incomplete'; END IF; END $$"
        )
        for column in ("private_ciphertext", "private_nonce", "private_key_id"):
            op.execute(
                f"ALTER TABLE {table} ALTER COLUMN {column} SET NOT NULL"
            )

    for column in (
        "title",
        "prompt_text",
        "ui_payload_json",
        "agent_payload_json",
        "decision_payload_json",
        "runtime_correlation_json",
        "resume_payload_json",
        "interaction_result_json",
    ):
        op.execute(f"ALTER TABLE hitl_requests DROP COLUMN IF EXISTS {column}")
    for column in (
        "title",
        "definition_json",
        "widget_state_json",
        "interaction_result_json",
        "artifact_ref",
    ):
        op.execute(
            f"ALTER TABLE interactive_artifacts DROP COLUMN IF EXISTS {column}"
        )


def downgrade() -> None:
    raise RuntimeError(
        "revision 079 is intentionally irreversible: HITL plaintext columns "
        "cannot be restored"
    )
