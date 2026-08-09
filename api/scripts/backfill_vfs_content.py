"""Backfill legacy VFS `content`-column rows into the ObjectStore.

RUN THIS BEFORE the 020_drop_vfs_content migration. Post-unification every VFS
write is object-backed (bytes in the ObjectStore at `object_key`, Postgres = pure
metadata index). The `content` column only holds data for PRE-unification
(legacy) rows. This one-shot script moves each such row's text into the
ObjectStore at the EXACT key the bytes-writers use, then sets `object_key` and
clears `content` — so the subsequent column DROP loses no data.

A fresh/dev/CI DB has no legacy rows (content="" everywhere or the column was
never created) → this is a NO-OP. Re-running is idempotent (rows already
object-backed are skipped).

For each row in `vfs_artifacts` then `vfs_scratch` with `object_key IS NULL`
AND `content <> ''`:
    key = artifacts/{tenant}/{scope_id}{path}   (vfs_store.write_artifact_bytes)
    key = scratch/{tenant}/{scope_id}{path}     (vfs_store.write_scratch_bytes)
    object_store.put_bytes(key, content.encode(), content_type=<row.content_type>)
    UPDATE ... SET object_key = key, content = ''

Uses the admin (RLS-bypassing) engine so it sees every tenant's rows in one
pass; the key is built from each row's own `tenant_id` so RLS is irrelevant.

Run against a running stack / a DB with the column still present:
    DATABASE_URL=postgresql+asyncpg://dev:dev@localhost:5432/vibecanvas \
        python api/scripts/backfill_vfs_content.py
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.storage.sync_session import short_admin_connection

# (table, key_prefix) — key layout MUST match vfs_store.py bytes-writers exactly:
#   write_artifact_bytes / upsert_artifact_bytes: artifacts/{tenant}/{scope_id}{path}
#   write_scratch_bytes:                          scratch/{tenant}/{scope_id}{path}
_TABLES = (
    ("vfs_artifacts", "artifacts"),
    ("vfs_scratch", "scratch"),
)


async def _backfill_table(conn, table: str, key_prefix: str) -> tuple[int, int]:
    """Move every legacy `content` row in `table` to the ObjectStore.

    Returns (moved, skipped). Skips rows that are already object-backed.
    `content <> ''` is part of the WHERE so empty rows are never touched.
    """
    store = get_object_store()
    rows = (await conn.execute(text(
        f"SELECT tenant_id, scope_id, path, content, content_type "
        f"FROM {table} "
        f"WHERE object_key IS NULL AND content <> ''"
    ))).mappings().all()

    moved = 0
    for r in rows:
        key = f"{key_prefix}/{r['tenant_id']}/{r['scope_id']}{r['path']}"
        store.put_bytes(key, r["content"].encode(), content_type=r["content_type"])
        await conn.execute(
            text(f"UPDATE {table} SET object_key = :k, content = '' "
                 f"WHERE scope_id = :w AND path = :p"),
            {"k": key, "w": r["scope_id"], "p": r["path"]})
        moved += 1
    return moved, len(rows)


async def _main() -> int:
    async with short_admin_connection() as conn:
        # Guard: if the column was already dropped there is nothing to backfill.
        col = (await conn.execute(text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'vfs_artifacts' AND column_name = 'content'"
        ))).first()
        if col is None:
            print("vfs_artifacts.content already dropped — nothing to backfill.")
            return 0

        async with conn.begin():
            total_moved = 0
            for table, key_prefix in _TABLES:
                moved, found = await _backfill_table(conn, table, key_prefix)
                print(f"{table}: {moved} legacy row(s) moved to ObjectStore "
                      f"({key_prefix}/...).")
                total_moved += moved
    print(f"Backfill done: {total_moved} row(s) moved. Safe to run migration 020.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
