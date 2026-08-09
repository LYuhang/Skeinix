import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


@pytest.mark.asyncio
async def test_postgres_saver_setup_and_roundtrip(pg_url):
    # NOTE: the installed langgraph-checkpoint-postgres (2.0.25) `aput`
    # requires a full RunnableConfig (incl. ``checkpoint_ns``) and a
    # well-formed ``Checkpoint`` dict — the plan's verbatim
    # ``aput(cfg, {"v": 1}, {}, {})`` raises ``KeyError: 'checkpoint_ns'``
    # against this version. Adapted to the real API contract while
    # keeping the original intent: setup() + put + get round-trips a
    # checkpoint against the pytest-postgresql DB.
    async with AsyncPostgresSaver.from_conn_string(
        pg_url.replace("+asyncpg", "")) as cp:
        await cp.setup()
        cfg = {"configurable": {"thread_id": "t-smoke", "checkpoint_ns": ""}}
        checkpoint = empty_checkpoint()
        await cp.aput(cfg, checkpoint, {}, {})
        got = await cp.aget(cfg)
        assert got is not None
        assert got["id"] == checkpoint["id"]
