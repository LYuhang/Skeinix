# -*- coding: utf-8 -*-
"""RE-1 T7 — run-tier verification.

Two proofs:

1. RLS tenant isolation: tenant B's repo (its own tenant-scoped session under
   the FORCE-RLS ``vibecanvas_app`` role) cannot read tenant A's ``vfs_run``
   row, even though A committed it. This mirrors the cross-tenant assertions in
   ``test_vfs_store`` / ``test_sync_tenant`` — write+commit as tenant A, then
   read as tenant B → ``None`` because the ``vfs_run`` RLS policy filters on
   ``tenant_id = current_setting('app.tenant_id')``.

   IMPORTANT: the two repos run on SEPARATE tenant-scoped sessions (mirroring
   the conftest ``vfs_run_repo`` fixture twice). For B's read to be a genuine
   RLS test (not merely MVCC transaction isolation hiding an uncommitted row),
   A's write is COMMITTED first — so the row is durable + globally visible, and
   the ONLY reason B gets ``None`` is the RLS tenant predicate.

2. materialize-then-plain-process-reads-same-bytes: the RE-6 sandbox-mount
   stand-in. After ``materialize()`` returns a real host dir, a plain
   ``os.open``/``open`` reads byte-for-byte what the node tools wrote — proving
   the real-FS seam the sandbox will mount.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from vibecanvas_api.services.object_store import FilesystemObjectStore
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.vfs_run_repo import VfsRunRepo


class _CommittingVfsRunRepo(VfsRunRepo):
    """VfsRunRepo whose ``write_bytes`` COMMITS its tenant-scoped session, so a
    second tenant's SEPARATE session/transaction can observe (and then be RLS-
    denied) the durable row. Without the commit, B's separate transaction would
    miss A's row by MVCC alone — masking whether RLS is actually doing the work.

    ``app.tenant_id`` is set with ``is_local=true`` (transaction-scoped) by
    ``session_scope``, so a commit clears it. We re-apply the GUC after each
    commit so the NEXT statement (e.g. ``read``) still runs under this tenant's
    RLS context — otherwise the read hits ``current_setting('app.tenant_id')``
    == '' and the uuid cast in the RLS predicate errors. All other methods are
    inherited unchanged."""

    async def write_bytes(self, **kw):
        path = await super().write_bytes(**kw)
        await self._s.commit()
        await self._s.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": self._t})
        return path


@pytest_asyncio.fixture
async def two_tenant_run_repos(app_engine, tmp_path):
    """Two ``VfsRunRepo``s, each bound to its OWN tenant + its OWN
    tenant-scoped ``session_scope`` session under the FORCE-RLS
    ``vibecanvas_app`` role. They SHARE one ``FilesystemObjectStore`` tmpdir
    root — the object key embeds the tenant, so bytes never collide, and RLS is
    enforced at the Postgres ``vfs_run`` row level, not the blob store.

    Mirrors the single-tenant ``vfs_run_repo`` conftest fixture, twice.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    async with app_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'a')"),
            {"t": tenant_a})
        await c.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'b')"),
            {"t": tenant_b})
    store = FilesystemObjectStore(root=str(tmp_path))
    async with session_scope(tenant_id=str(tenant_a)) as sa, \
            session_scope(tenant_id=str(tenant_b)) as sb:
        repo_a = _CommittingVfsRunRepo(sa, store, str(tenant_a))
        repo_b = _CommittingVfsRunRepo(sb, store, str(tenant_b))
        yield repo_a, repo_b
        await sa.rollback()
        await sb.rollback()


@pytest.mark.asyncio
async def test_run_tier_tenant_isolation(two_tenant_run_repos):
    """RLS: tenant B cannot see tenant A's run row."""
    repo_a, repo_b = two_tenant_run_repos
    await repo_a.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"secret", content_type="text/plain")
    assert await repo_a.read(run_id="r1", path="/run/n1/a.txt") is not None
    assert await repo_b.read(run_id="r1", path="/run/n1/a.txt") is None   # RLS blocks cross-tenant


@pytest.mark.asyncio
async def test_materialize_then_plain_process_reads_same_bytes(vfs_run_repo):
    """The RE-6 sandbox-mount stand-in: after materialize(), a plain os/open reads
    the SAME bytes the node tools wrote — proving the real-FS seam."""
    png = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03"
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.bin", data=png, content_type="application/octet-stream")
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/sub/c.txt", data=b"deep", content_type="text/plain")
    d = vfs_run_repo.materialize(run_id="r1")
    with open(os.path.join(d, "n1", "a.bin"), "rb") as f:
        assert f.read() == png
    with open(os.path.join(d, "n1", "sub", "c.txt"), "rb") as f:
        assert f.read() == b"deep"
