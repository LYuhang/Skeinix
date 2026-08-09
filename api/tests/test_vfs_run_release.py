"""RE-1 T4 — auto-release at execution end (E0) + materialize-to-real-dir seam.

`release` deletes the vfs_run metadata rows (RLS-bound) then best-effort deletes
the bytes — idempotent (2nd release is a no-op) + crash-tolerant. `retain=True`
keeps everything (debug-execute). `materialize` returns the real host dir mirroring
the run's files (RE-6 mounts it). Shares the `vfs_run_repo` fixture from conftest.
"""
import os

import pytest


@pytest.mark.asyncio
async def test_release_deletes_rows_and_bytes(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"a", content_type="text/plain")
    await vfs_run_repo.release(run_id="r1")
    assert await vfs_run_repo.read(run_id="r1", path="/run/n1/a.txt") is None
    # idempotent — a second release does not raise
    await vfs_run_repo.release(run_id="r1")


@pytest.mark.asyncio
async def test_release_retain_keeps(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"a", content_type="text/plain")
    await vfs_run_repo.release(run_id="r1", retain=True)
    assert await vfs_run_repo.read(run_id="r1", path="/run/n1/a.txt") is not None


@pytest.mark.asyncio
async def test_materialize_yields_real_dir(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"hi", content_type="text/plain")
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/sub/b.bin", data=b"\x00\x01", content_type="application/octet-stream")
    d = vfs_run_repo.materialize(run_id="r1")
    assert os.path.isdir(d)
    with open(os.path.join(d, "n1", "a.txt"), "rb") as f:
        assert f.read() == b"hi"
    with open(os.path.join(d, "n1", "sub", "b.bin"), "rb") as f:
        assert f.read() == b"\x00\x01"
