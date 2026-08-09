"""KB / RAG T1 — ``KbRepo`` CRUD + soft-delete + dedup + RLS smoke.

Coverage (5 cases — match the spec in plan Step 1.6):

* ``create_kb`` + ``list_active`` round-trip writes + reads the same row.
* Unique-name-per-tenant: a second ``create_kb`` with the same ``name``
  while the first is still alive raises (partial UNIQUE on
  ``(tenant_id, name) WHERE deleted_at IS NULL``).
* ``soft_delete_kb`` cascades the ``deleted_at`` UPDATE to ``kb_files``
  so ``list_files`` returns ``[]`` immediately.
* Per-KB ``content_hash`` dedup: a second ``create_file`` with the same
  hash on the same KB while the first is alive raises (partial UNIQUE
  on ``(kb_id, content_hash) WHERE deleted_at IS NULL``).
* Cross-tenant RLS: a KB created under tenant A is invisible from a
  session bound to tenant B (``get_active`` returns ``None``).

Fixture pattern follows ``test_mcp_servers_repo.py``: the plan-spec
``tenant_session`` / ``db_session`` / ``tenant_id`` / ``user_id``
fixtures are NOT present in this repo's conftest, so we inline tenant +
user seeding through the RLS-bypassing ``pg_engine`` and then drive
``KbRepo`` through ``session_scope(tenant_id=...)`` — exactly the same
choice MCP T1/T2 made when it hit the identical gap.
"""
from __future__ import annotations

import uuid

import pytest

from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models_kb import KbChunk
from vibecanvas_api.storage.repo_kb import KbRepo
from sqlalchemy import text


# --------------------------------------------------------------------- seed


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
    """Insert tenant + user via the RLS-bypassing superuser engine. The
    auth tables are RLS-free so a plain ``begin()`` block suffices."""
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tenant_id},
        )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": user_id, "t": tenant_id,
             "e": f"kb-repo-{uuid.uuid4().hex[:6]}@example.com"},
        )


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_create_and_list_kb(pg_engine):
    """``create_kb`` + ``list_active`` round-trip."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        kb = await repo.create_kb(
            tenant_id=tenant_id, user_id=user_id, name="HR Docs",
        )
        await s.commit()
        assert kb.id is not None
        kb_id = kb.id

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        listed = await repo.list_active()
    assert any(k.id == kb_id for k in listed)


@pytest.mark.asyncio
async def test_unique_name_per_tenant_active(pg_engine):
    """Partial UNIQUE on (tenant_id, name) WHERE deleted_at IS NULL —
    two live rows with the same name in the same tenant must error."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        await repo.create_kb(
            tenant_id=tenant_id, user_id=user_id, name="HR",
        )
        await s.commit()

    with pytest.raises(Exception):
        async with session_scope(tenant_id=str(tenant_id)) as s:
            repo = KbRepo(s)
            await repo.create_kb(
                tenant_id=tenant_id, user_id=user_id, name="HR",
            )
            # commit triggers the constraint violation if flush didn't.
            await s.commit()


@pytest.mark.asyncio
async def test_soft_delete_kb_cascades_to_files(pg_engine):
    """``soft_delete_kb`` must propagate ``deleted_at`` to every live
    ``kb_files`` row so ``list_files`` immediately stops returning them
    (spec sec 4.6 contract)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        kb = await repo.create_kb(
            tenant_id=tenant_id, user_id=user_id, name="X",
        )
        await repo.create_file(
            kb_id=kb.id, tenant_id=tenant_id, user_id=user_id,
            name="a.pdf", parser_type="pdf",
            mime_type="application/pdf",
            file_size=100, content_hash="h" * 64,
        )
        await s.commit()
        kb_id = kb.id

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        await repo.soft_delete_kb(kb_id)
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        files = await repo.list_files(kb_id)
    assert files == []


@pytest.mark.asyncio
async def test_soft_deleted_file_chunks_are_excluded_from_count(pg_engine):
    """KB detail counters use the same live-file boundary as retrieval."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        kb = await repo.create_kb(
            tenant_id=tenant_id, user_id=user_id, name="Chunk count",
        )
        source = await repo.create_file(
            kb_id=kb.id,
            tenant_id=tenant_id,
            user_id=user_id,
            name="source.md",
            parser_type="markdown",
            mime_type="text/markdown",
            file_size=10,
            content_hash="c" * 64,
            status="indexed",
        )
        await repo.bulk_insert_chunks([
            KbChunk(
                file_id=source.id,
                kb_id=kb.id,
                tenant_id=tenant_id,
                chunk_index=0,
                text="Temporary acceptance content.",
                chunk_metadata={},
            ),
        ])
        assert await repo.count_chunks(kb.id) == 1

        await repo.soft_delete_file(source.id)
        assert await repo.count_chunks(kb.id) == 0


@pytest.mark.asyncio
async def test_content_hash_dedup(pg_engine):
    """Partial UNIQUE on (kb_id, content_hash) WHERE deleted_at IS NULL
    — two live files with the same hash in the same KB must error."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        kb = await repo.create_kb(
            tenant_id=tenant_id, user_id=user_id, name="X",
        )
        await repo.create_file(
            kb_id=kb.id, tenant_id=tenant_id, user_id=user_id,
            name="a.pdf", parser_type="pdf",
            mime_type="application/pdf",
            file_size=100, content_hash="abc" + "0" * 61,
        )
        await s.commit()
        kb_id = kb.id

    with pytest.raises(Exception):
        async with session_scope(tenant_id=str(tenant_id)) as s:
            repo = KbRepo(s)
            await repo.create_file(
                kb_id=kb_id, tenant_id=tenant_id, user_id=user_id,
                name="a-renamed.pdf", parser_type="pdf",
                mime_type="application/pdf",
                file_size=100, content_hash="abc" + "0" * 61,
            )
            await s.commit()


@pytest.mark.asyncio
async def test_cross_tenant_rls_blocks(pg_engine):
    """A KB created in tenant A is invisible from a session bound to
    tenant B (RLS ``tenant_isolation`` policy on ``knowledge_bases``)."""
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    user_id = uuid.uuid4()
    # tenant_a gets a user; tenant_b only needs to exist as a tenant.
    await _seed_tenant_and_user(pg_engine, tenant_a, user_id)
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'b')"),
            {"t": tenant_b},
        )

    async with session_scope(tenant_id=str(tenant_a)) as s:
        repo = KbRepo(s)
        kb = await repo.create_kb(
            tenant_id=tenant_a, user_id=user_id, name="Secret",
        )
        await s.commit()
        kb_id = kb.id

    async with session_scope(tenant_id=str(tenant_b)) as s:
        repo = KbRepo(s)
        found = await repo.get_active(kb_id)
    assert found is None, (
        "RLS tenant_isolation policy must hide tenant A's KB from a "
        "session bound to tenant B"
    )
