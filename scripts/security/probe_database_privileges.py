#!/usr/bin/env python3
"""Deployment gate for runtime and maintenance PostgreSQL roles."""
from __future__ import annotations

import asyncio

from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import create_async_engine

from vibecanvas_api.config import config
from vibecanvas_api.security.database_privileges import verify_database_role
from vibecanvas_api.storage.db import maintenance_database_url


async def _main() -> None:
    runtime = create_async_engine(
        config.database.url,
        poolclass=NullPool,
        connect_args={"prepared_statement_cache_size": 0},
    )
    maintenance = create_async_engine(
        maintenance_database_url(),
        poolclass=NullPool,
        connect_args={"prepared_statement_cache_size": 0},
    )
    try:
        await verify_database_role(runtime, mode="runtime")
        await verify_database_role(maintenance, mode="maintenance")
    finally:
        await runtime.dispose()
        await maintenance.dispose()
    print("database privilege probe passed: runtime + maintenance")


if __name__ == "__main__":
    asyncio.run(_main())
