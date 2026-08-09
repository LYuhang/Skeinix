"""Add migration-only Knowledge Base ciphertext envelopes.

Revision ID: 071
Revises: 070
Create Date: 2026-07-31

The running application already uses the strict ciphertext-only ORM. Existing
installations pause here so the host migration command can encrypt private KB
metadata, filenames/errors, chunk text/metadata, and embeddings before 072
permanently removes their plaintext columns.
"""
from alembic import op


revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def _add_envelope(table: str, prefix: str) -> None:
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_ciphertext text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_nonce text"
    )
    op.execute(
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {prefix}_key_id uuid"
    )
    op.execute(
        f"DO $$ BEGIN IF NOT EXISTS ("
        "SELECT 1 FROM information_schema.table_constraints tc "
        "JOIN information_schema.key_column_usage kcu "
        "ON tc.constraint_name=kcu.constraint_name "
        "AND tc.table_schema=kcu.table_schema "
        "WHERE tc.table_schema=current_schema() "
        f"AND tc.table_name='{table}' AND tc.constraint_type='FOREIGN KEY' "
        f"AND kcu.column_name='{prefix}_key_id') THEN "
        f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_{prefix}_key "
        f"FOREIGN KEY ({prefix}_key_id) "
        "REFERENCES content_encryption_keys(key_id) ON DELETE RESTRICT; "
        "END IF; END $$"
    )


def upgrade() -> None:
    op.execute(
        "ALTER TABLE knowledge_bases ADD COLUMN IF NOT EXISTS "
        "name_lookup_hash varchar(64)"
    )
    _add_envelope("knowledge_bases", "private")
    _add_envelope("kb_files", "private")
    _add_envelope("kb_chunks", "content")


def downgrade() -> None:
    raise RuntimeError(
        "revision 071 contains migration ciphertext and is not downgradable"
    )
