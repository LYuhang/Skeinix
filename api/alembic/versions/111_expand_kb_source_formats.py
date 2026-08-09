"""Expand Knowledge source parser types.

Revision ID: 111
Revises: 110
Create Date: 2026-08-02
"""

from alembic import op


revision = "111"
down_revision = "110"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE kb_files DROP CONSTRAINT IF EXISTS ck_kb_files_parser_type"
    )
    op.execute(
        "ALTER TABLE kb_files ADD CONSTRAINT ck_kb_files_parser_type "
        "CHECK (parser_type IN "
        "('pdf','markdown','txt','docx','pptx','xlsx','csv','json','html'))"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE kb_files DROP CONSTRAINT IF EXISTS ck_kb_files_parser_type"
    )
    op.execute(
        "ALTER TABLE kb_files ADD CONSTRAINT ck_kb_files_parser_type "
        "CHECK (parser_type IN ('pdf','markdown','txt','docx'))"
    )
