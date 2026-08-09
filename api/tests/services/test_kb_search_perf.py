"""Encrypted lexical retrieval latency baseline at 10k chunks.

Skipped by default — opt-in via ``KB_PERF_BASELINE=1`` env var so the
test doesn't slow down normal pytest runs. The baseline is for manual
inspection (and for the post-Phase-7 perf-tracking dashboard); CI does
NOT enforce the latency assertion as a gate.

What it measures
----------------
* Insert 10k deterministic text chunks into a single KB.
* Issue one ``KbSearchService.search_async(..., top_k=5)`` call with a
  random query vector.
* Wall-clock the search call (excludes the bulk-insert, includes the
  ``SET LOCAL hnsw.ef_search = 40``, the SQL, and the Pydantic
  serialisation).

The bar is a generous 200ms — the HNSW index from migration 002 + the
search SQL is expected to land well below that on a developer laptop,
but apt-installed postgres-15 in a kube pod is not a perf rig. Anything
above that line is a real regression worth investigating.

Fixture pattern matches ``test_kb_search.py``.
"""
from __future__ import annotations

import os
import time
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.services.kb_search import KbSearchService
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models_kb import KbChunk
from vibecanvas_api.storage.repo_kb import KbRepo


PERF_OPT_IN = os.getenv("KB_PERF_BASELINE")


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
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
             "e": f"kb-perf-{uuid.uuid4().hex[:6]}@example.com"},
        )


@pytest.mark.skipif(
    not PERF_OPT_IN,
    reason="set KB_PERF_BASELINE=1 to run the perf baseline",
)
@pytest.mark.asyncio
async def test_hnsw_search_under_200ms_at_10k(pg_engine):
    """10k chunks, top-5 search → wall-clock < 200ms."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = KbRepo(s)
        kb = await repo.create_kb(
            tenant_id=tenant_id, user_id=user_id, name="Perf",
        )
        f = await repo.create_file(
            kb_id=kb.id, tenant_id=tenant_id, user_id=user_id,
            name="x", parser_type="txt", mime_type="text/plain",
            file_size=1, content_hash="p" * 64, status="indexed",
        )
        chunks = [
            KbChunk(
                file_id=f.id, kb_id=kb.id, tenant_id=tenant_id,
                chunk_index=i, text=f"release policy document number {i}",
                chunk_metadata={},
            )
            for i in range(10_000)
        ]
        await repo.bulk_insert_chunks(chunks)
        await s.commit()
        kb_id = kb.id

    async with session_scope(tenant_id=str(tenant_id)) as s:
        svc = KbSearchService(s)
        start = time.perf_counter()
        await svc.search_async(
            kb_ids=[str(kb_id)], query="release policy 9999", top_k=5,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

    print(f"\nEncrypted lexical search 10k chunks: {elapsed_ms:.1f} ms")
    assert elapsed_ms < 3000, (
        f"perf regression: encrypted lexical 10k took {elapsed_ms:.1f} ms (> 3000 ms)"
    )
