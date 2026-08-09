"""DDL-free runtime verification for LangGraph checkpoint storage."""
from __future__ import annotations

from langgraph.checkpoint.postgres.base import BasePostgresSaver


async def verify_checkpointer_schema(pool) -> None:
    """Fail when the one-shot migration workload did not run ``setup()``."""
    expected = len(BasePostgresSaver.MIGRATIONS) - 1
    try:
        async with pool.connection() as connection:
            result = await connection.execute(
                "SELECT max(v) AS version FROM checkpoint_migrations"
            )
            row = await result.fetchone()
    except Exception as exc:
        raise RuntimeError(
            "runtime checkpointer schema is missing; run the migration workload"
        ) from exc
    actual = -1 if row is None or row["version"] is None else int(row["version"])
    if actual < expected:
        raise RuntimeError(
            "runtime checkpointer schema is stale; run the migration workload"
        )
