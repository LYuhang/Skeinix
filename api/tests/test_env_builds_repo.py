"""EnvBuildsRepo over the global, content-addressed ``env_builds``
table (the Python-library overlay build registry).

``env_builds`` is deliberately tenant-agnostic + has NO RLS: an overlay is
public-PyPI content shared across all tenants, keyed by a content hash of the
declared requirements. The repo therefore uses a NON-tenant (admin) session.

These tests bind directly to the superuser ``pg_engine`` (which is exactly the
engine the test conftest injects as ``db._admin_engine`` everywhere else) and
exercise the repo against it — no tenant GUC, no RLS in play.
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from vibecanvas_api.storage.repo_env_builds import EnvBuildsRepo


_KEY = "a" * 64
_REQS = "pandas==2.2.0\nnumpy==1.26.4\n"


@pytest.mark.asyncio
async def test_upsert_building_then_get(pg_engine):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        repo = EnvBuildsRepo(s)
        await repo.upsert_building(_KEY, _REQS)
        await s.commit()
        row = await repo.get(_KEY)
    assert row is not None
    assert row["overlay_key"] == _KEY
    assert row["status"] == "building"
    assert row["requirements"] == _REQS
    assert row["built_at"] is None
    assert row["created_at"] is not None


@pytest.mark.asyncio
async def test_mark_ready(pg_engine):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        repo = EnvBuildsRepo(s)
        await repo.upsert_building(_KEY, _REQS)
        await repo.mark_ready(_KEY)
        await s.commit()
        row = await repo.get(_KEY)
    assert row["status"] == "ready"
    assert row["built_at"] is not None


@pytest.mark.asyncio
async def test_mark_failed(pg_engine):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        repo = EnvBuildsRepo(s)
        await repo.upsert_building(_KEY, _REQS)
        await repo.mark_failed(_KEY, "pip: could not find a version")
        await s.commit()
        row = await repo.get(_KEY)
    assert row["status"] == "failed"
    assert row["error_log"] == "pip: could not find a version"


@pytest.mark.asyncio
async def test_upsert_idempotent(pg_engine):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        repo = EnvBuildsRepo(s)
        await repo.upsert_building(_KEY, _REQS)
        # A second upsert (e.g. a stale/failed build retried) must not create a
        # duplicate row — ON CONFLICT resets status=building.
        await repo.mark_failed(_KEY, "boom")
        await repo.upsert_building(_KEY, "pandas==2.2.0\n")
        await s.commit()
        async with pg_engine.connect() as c:
            from sqlalchemy import text
            n = (await c.execute(
                text("SELECT count(*) FROM env_builds WHERE overlay_key = :k"),
                {"k": _KEY},
            )).scalar_one()
        row = await repo.get(_KEY)
    assert n == 1
    assert row["status"] == "building"
    assert row["requirements"] == "pandas==2.2.0\n"


@pytest.mark.asyncio
async def test_get_missing(pg_engine):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        repo = EnvBuildsRepo(s)
        row = await repo.get("f" * 64)
    assert row is None
