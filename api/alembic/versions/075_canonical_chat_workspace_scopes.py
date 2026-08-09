"""Replace user-prefixed Chat workspace scopes with canonical Chat scopes.

Revision ID: 075
Revises: 074
Create Date: 2026-07-31

The previous scope format embedded a creator prefix and was incorrectly used by
some HTTP paths as authorization evidence.  This one-time migration rewrites
the metadata projection.  Runtime code understands only the new reversible
Chat-id encoding and always authorizes the decoded Chat through OpenFGA.
Object keys intentionally remain opaque and unchanged; each row still points to
the same encrypted blob and normal delete/overwrite paths continue to own it.
"""
from alembic import op


revision = "075"
down_revision = "074"
branch_labels = None
depends_on = None


_MAPPING = """
SELECT
    c.tenant_id,
    '__chatws_' || left(replace(c.creator_user_id::text, '-', ''), 20) || '_' ||
        CASE
            WHEN regexp_replace(c.chat_id, '[^[:alnum:]]', '', 'g') = ''
                THEN 'default'
            ELSE left(regexp_replace(c.chat_id, '[^[:alnum:]]', '', 'g'), 32)
        END AS old_scope_id,
    '__chatws_v2_' || rtrim(
        translate(
            encode(convert_to(c.chat_id, 'UTF8'), 'base64'),
            E'+/\n',
            '-_'
        ),
        '='
    ) AS new_scope_id
FROM chats AS c
"""


def upgrade() -> None:
    for table in ("vfs_artifacts", "vfs_scratch"):
        op.execute(
            f"""
            UPDATE {table} AS target
               SET scope_id = mapping.new_scope_id
              FROM ({_MAPPING}) AS mapping
             WHERE target.tenant_id = mapping.tenant_id
               AND target.scope_id = mapping.old_scope_id
            """
        )
    op.execute(
        f"""
        UPDATE vfs_artifact_events AS target
           SET scope_id = mapping.new_scope_id
          FROM ({_MAPPING}) AS mapping
         WHERE target.tenant_id = mapping.tenant_id
           AND target.scope_kind = 'artifact'
           AND target.scope_id = mapping.old_scope_id
        """
    )
    op.execute(
        "DO $$ BEGIN IF EXISTS ("
        "SELECT 1 FROM vfs_artifacts WHERE scope_id LIKE '__chatws_%' "
        "AND scope_id NOT LIKE '__chatws_v2_%' UNION ALL "
        "SELECT 1 FROM vfs_scratch WHERE scope_id LIKE '__chatws_%' "
        "AND scope_id NOT LIKE '__chatws_v2_%'"
        ") THEN RAISE EXCEPTION "
        "'Chat workspace scope migration incomplete'; END IF; END $$"
    )


def downgrade() -> None:
    raise RuntimeError(
        "revision 075 is intentionally irreversible: user-prefixed Chat "
        "workspace authorization must not return"
    )
