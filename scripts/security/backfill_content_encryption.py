#!/usr/bin/env python3
"""Backfill Chat/Workflow ciphertext without changing API or SSE payloads."""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from vibecanvas_api.security.content_backfill import backfill_chat, backfill_workflow
from vibecanvas_api.services.tenant_db import session_scope_admin
from vibecanvas_api.storage.db import session_scope


async def _candidates(limit: int) -> list[tuple[str, str, str]]:
    query = text(
        """
        SELECT kind, tenant_id::text, resource_id FROM (
          SELECT 'chat' AS kind, c.tenant_id, c.chat_id AS resource_id,
                 min(m.id)::bigint AS ordering
            FROM chats c JOIN chat_messages m ON m.chat_id = c.chat_id
           WHERE m.content_key_id IS NULL
           GROUP BY c.tenant_id, c.chat_id
          UNION ALL
          SELECT 'workflow' AS kind, w.tenant_id, w.wf_id AS resource_id,
                 0::bigint AS ordering
            FROM workflows w JOIN workflow_versions v ON v.wf_id = w.wf_id
           WHERE v.workflow_key_id IS NULL
           GROUP BY w.tenant_id, w.wf_id
        ) pending
        ORDER BY kind, ordering, resource_id
        LIMIT :limit
        """
    )
    async with session_scope_admin() as session:
        return [tuple(row) for row in (await session.execute(query, {"limit": limit})).all()]


async def run(*, batch_size: int, max_resources: int) -> tuple[int, int]:
    resources = encrypted_rows = 0
    while resources < max_resources:
        pending = await _candidates(min(batch_size, max_resources - resources))
        if not pending:
            break
        for kind, tenant_id, resource_id in pending:
            async with session_scope(tenant_id=tenant_id) as session:
                if kind == "chat":
                    encrypted_rows += await backfill_chat(session, resource_id)
                else:
                    encrypted_rows += await backfill_workflow(session, resource_id)
            resources += 1
    return resources, encrypted_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=25)
    parser.add_argument("--max-resources", type=int, default=10_000)
    args = parser.parse_args()
    resources, rows = asyncio.run(run(
        batch_size=max(1, args.batch_size),
        max_resources=max(1, args.max_resources),
    ))
    print(f"encrypted {rows} rows across {resources} resources")


if __name__ == "__main__":
    main()
