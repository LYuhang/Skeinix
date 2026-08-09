from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from langgraph.checkpoint.postgres.base import BasePostgresSaver
from vibecanvas_api.security.checkpointer_schema import (
    verify_checkpointer_schema,
)


class _Result:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row=None, error: Exception | None = None):
        self._row = row
        self._error = error

    async def execute(self, _query):
        if self._error:
            raise self._error
        return _Result(self._row)


class _Pool:
    def __init__(self, row=None, error: Exception | None = None):
        self._connection = _Connection(row, error)

    @asynccontextmanager
    async def connection(self):
        yield self._connection


@pytest.mark.asyncio
async def test_current_checkpointer_schema_is_accepted():
    expected = len(BasePostgresSaver.MIGRATIONS) - 1
    await verify_checkpointer_schema(_Pool({"version": expected}))


@pytest.mark.asyncio
async def test_stale_checkpointer_schema_is_rejected():
    with pytest.raises(RuntimeError, match="stale"):
        await verify_checkpointer_schema(_Pool({"version": -1}))


@pytest.mark.asyncio
async def test_missing_checkpointer_schema_is_rejected():
    with pytest.raises(RuntimeError, match="missing"):
        await verify_checkpointer_schema(_Pool(error=LookupError("missing")))
