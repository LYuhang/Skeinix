"""RE-1 T3 — VfsRunRepo: binary write/read + run-keyed read/ls over the ObjectStore.

The `vfs_run_repo` fixture mirrors `test_vfs_store.py`'s RLS pattern: seed a real
`tenants` row (auth table, no RLS), then open an app_engine session bound to that
tenant via `session_scope(tenant_id=...)` (sets app.tenant_id GUC so the vfs_run
tenant_id FetchedValue() DEFAULT + FORCE RLS resolve). Bytes go to a real
FilesystemObjectStore rooted at a tmpdir (NOT InMemory — we want real-file behavior).

The `vfs_run_repo` fixture lives in tests/conftest.py (RE-1 T4 moved it there so
test_vfs_run_release shares it).
"""
import pytest


@pytest.mark.asyncio
async def test_write_read_bytes_roundtrip_binary(vfs_run_repo):
    png = b"\x89PNG\r\n\x1a\n\x00\x01\x02"
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/img.png",
                                   data=png, content_type="image/png")
    assert await vfs_run_repo.read_bytes(run_id="r1", path="/run/n1/img.png") == png  # byte-identical
    entry = await vfs_run_repo.read(run_id="r1", path="/run/n1/img.png")
    assert entry is not None and entry.content_type == "image/png" and entry.size_bytes == len(png)


@pytest.mark.asyncio
async def test_text_roundtrip(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/note.txt", data=b"hello", content_type="text/plain")
    assert (await vfs_run_repo.read_bytes(run_id="r1", path="/run/n1/note.txt")).decode() == "hello"


@pytest.mark.asyncio
async def test_run_abstract_is_ciphertext_only(vfs_run_repo):
    from sqlalchemy import text

    await vfs_run_repo.write_bytes(
        run_id="r1",
        path="/run/n1/private.txt",
        data=b"hello",
        content_type="text/plain",
        abstract="customer secret summary",
    )
    stored = (
        await vfs_run_repo._s.execute(
            text(
                "SELECT abstract, abstract_ciphertext, abstract_nonce, "
                "abstract_key_id FROM vfs_run "
                "WHERE run_id='r1' AND path='/run/n1/private.txt'"
            )
        )
    ).mappings().one()
    assert stored["abstract"] == ""
    assert stored["abstract_key_id"] is not None
    assert stored["abstract_nonce"]
    assert stored["abstract_ciphertext"]
    assert "customer secret summary" not in stored["abstract_ciphertext"]


@pytest.mark.asyncio
async def test_path_escape_rejected(vfs_run_repo):
    with pytest.raises(ValueError):
        await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/../../etc/x", data=b"x", content_type="text/plain")
    with pytest.raises(ValueError):
        await vfs_run_repo.read(run_id="r1", path="/etc/passwd")   # not under /run/


@pytest.mark.asyncio
async def test_ls_run_scoped(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"a", content_type="text/plain")
    await vfs_run_repo.write_bytes(run_id="r2", path="/run/n1/b.txt", data=b"b", content_type="text/plain")
    paths = sorted(e.path for e in await vfs_run_repo.ls(run_id="r1", prefix="/run/"))
    assert paths == ["/run/n1/a.txt"]   # only r1


@pytest.mark.asyncio
async def test_object_key_strips_run_prefix(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"a", content_type="text/plain")
    entry = await vfs_run_repo.read(run_id="r1", path="/run/n1/a.txt")
    # object key strips the leading /run/ so the materialized tree is clean
    assert entry.object_key.endswith("/r1/n1/a.txt") and "/run/n1/" not in entry.object_key


# --- UX-10e0 "keep latest run per workflow" -------------------------------- #

@pytest.mark.asyncio
async def test_write_bytes_stamps_wf_id(vfs_run_repo):
    # wf_id is persisted onto the row so the next run can purge by workflow.
    from sqlalchemy import select
    from vibecanvas_api.storage.models import VfsRun
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"a",
                                   content_type="text/plain", wf_id="wf_1")
    row = (await vfs_run_repo._s.execute(select(VfsRun).where(
        VfsRun.run_id == "r1", VfsRun.path == "/run/n1/a.txt"))).scalar_one()
    assert row.wf_id == "wf_1"


@pytest.mark.asyncio
async def test_write_bytes_wf_id_defaults_null(vfs_run_repo):
    from sqlalchemy import select
    from vibecanvas_api.storage.models import VfsRun
    await vfs_run_repo.write_bytes(run_id="r1", path="/run/n1/a.txt", data=b"a",
                                   content_type="text/plain")
    row = (await vfs_run_repo._s.execute(select(VfsRun).where(
        VfsRun.run_id == "r1", VfsRun.path == "/run/n1/a.txt"))).scalar_one()
    assert row.wf_id is None


@pytest.mark.asyncio
async def test_purge_workflow_runs_deletes_other_runs_keeps_current(vfs_run_repo):
    # Three runs of wf_1 (old1, old2 = prior; cur = current) + one run of wf_2.
    await vfs_run_repo.write_bytes(run_id="old1", path="/run/n1/a.txt", data=b"a",
                                   content_type="text/plain", wf_id="wf_1")
    await vfs_run_repo.write_bytes(run_id="old1", path="/run/n1/b.txt", data=b"b",
                                   content_type="text/plain", wf_id="wf_1")
    await vfs_run_repo.write_bytes(run_id="old2", path="/run/n1/c.txt", data=b"c",
                                   content_type="text/plain", wf_id="wf_1")
    await vfs_run_repo.write_bytes(run_id="cur", path="/run/n1/d.txt", data=b"d",
                                   content_type="text/plain", wf_id="wf_1")
    await vfs_run_repo.write_bytes(run_id="other", path="/run/n1/e.txt", data=b"e",
                                   content_type="text/plain", wf_id="wf_2")

    purged = await vfs_run_repo.purge_workflow_runs(wf_id="wf_1", except_run_id="cur")
    assert purged == 2   # old1 + old2 (distinct run_ids), NOT cur, NOT other

    # prior runs gone (rows + blobs)
    assert await vfs_run_repo.read(run_id="old1", path="/run/n1/a.txt") is None
    assert await vfs_run_repo.read(run_id="old2", path="/run/n1/c.txt") is None
    # current run intact
    assert await vfs_run_repo.read(run_id="cur", path="/run/n1/d.txt") is not None
    assert await vfs_run_repo.read_bytes(run_id="cur", path="/run/n1/d.txt") == b"d"
    # other workflow's run intact
    assert await vfs_run_repo.read(run_id="other", path="/run/n1/e.txt") is not None


@pytest.mark.asyncio
async def test_purge_workflow_runs_no_prior_is_noop(vfs_run_repo):
    await vfs_run_repo.write_bytes(run_id="cur", path="/run/n1/d.txt", data=b"d",
                                   content_type="text/plain", wf_id="wf_1")
    assert await vfs_run_repo.purge_workflow_runs(wf_id="wf_1", except_run_id="cur") == 0
    assert await vfs_run_repo.read(run_id="cur", path="/run/n1/d.txt") is not None
    # falsy wf_id is a no-op
    assert await vfs_run_repo.purge_workflow_runs(wf_id="", except_run_id="cur") == 0
