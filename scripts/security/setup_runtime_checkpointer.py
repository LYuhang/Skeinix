#!/usr/bin/env python3
"""Create/upgrade LangGraph checkpoint tables from a one-shot workload."""
from __future__ import annotations

import asyncio
import os

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from vibecanvas_api.config import config
from vibecanvas_api.services.agent_runtime.checkpoint_store import (
    setup_runtime_state_schema,
)


async def _main() -> None:
    url = (
        os.environ.get("AGENT_RUNTIME_MIGRATION_DATABASE_URL")
        or os.environ.get("MIGRATION_DATABASE_URL")
        or config.agent_runtime_database_url
    )
    pool = AsyncConnectionPool(
        conninfo=url.replace("+asyncpg", ""),
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
        },
    )
    await pool.open()
    try:
        await AsyncPostgresSaver(conn=pool).setup()
        await setup_runtime_state_schema(pool)
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(_main())
