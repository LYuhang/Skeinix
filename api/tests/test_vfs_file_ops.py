"""VFS file ops (delete + rename) — repo-level tests.

Mirrors test_vfs_store.py: pure-repo tests on the superuser `pg_session` (no
RLS) with a seeded tenant/user/workflow and an InMemoryObjectStore, driving
`VfsRepo.delete_artifact` / `rename_artifact` directly. Route-layer auth is the
DEAD dev-token harness, so we cover the repo (where the logic lives) plus the
path-validation boundary the routes reuse.
"""
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.services.object_store import InMemoryObjectStore
from vibecanvas_api.storage.models import VfsArtifact
from vibecanvas_api.storage.vfs_store import VfsRepo
from vibecanvas_api.storage.workflow_repo import WorkflowRepo

PNG = b"\x89PNG\r\n\x1a\n\x00\xff\x10"


async def _seed_pg(pg_session):
    t, u = uuid.uuid4(), uuid.uuid4()
    await pg_session.execute(text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'x')"),
                             {"t": t})
    await pg_session.execute(
        text("INSERT INTO users(user_id,tenant_id,email) VALUES (:u,:t,:e)"),
        {"u": u, "t": t, "e": f"{u.hex[:6]}@example.com"})
    await pg_session.execute(text("SELECT set_config('app.tenant_id',:t,false)"),
                             {"t": str(t)})
    wf = await WorkflowRepo(pg_session, str(u)).create_workflow(name="W")
    return t.hex, wf["wf_id"]


@pytest.mark.asyncio
async def test_delete_artifact_removes_row_and_blob(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    os_ = InMemoryObjectStore()
    repo = VfsRepo(pg_session, object_store=os_)
    await repo.upsert_artifact_bytes(wf_id=wf_id, tenant=tenant,
                                     path="/mount/img.png", data=PNG,
                                     content_type="image/png")
    key = f"artifacts/{tenant}/{wf_id}/mount/img.png"
    assert os_.fetch_bytes(key) == PNG

    n = await repo.delete_artifact(wf_id=wf_id, tenant=tenant, path="/mount/img.png")
    assert n == 1
    assert await pg_session.get(VfsArtifact, (wf_id, "/mount/img.png")) is None
    with pytest.raises(KeyError):
        os_.fetch_bytes(key)


@pytest.mark.asyncio
async def test_delete_artifact_absent_returns_zero(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    assert await repo.delete_artifact(wf_id=wf_id, tenant=tenant,
                                      path="/data/nope.csv") == 0


@pytest.mark.asyncio
async def test_delete_folder_removes_all_children(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    for p in ("/data/sub/a.txt", "/data/sub/b.txt", "/data/sub/deep/c.txt"):
        await repo.upsert_artifact(wf_id=wf_id, tenant=tenant, path=p, content="x")
    # a sibling NOT under the folder must survive
    await repo.upsert_artifact(wf_id=wf_id, tenant=tenant, path="/data/keep.txt", content="k")

    n = await repo.delete_artifact(wf_id=wf_id, tenant=tenant, path="/data/sub")
    assert n == 3
    rows = {r.path for r in await repo.ls(wf_id=wf_id, prefix="/data/")}
    assert rows == {"/data/keep.txt"}


@pytest.mark.asyncio
async def test_rename_file_moves_bytes(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    await repo.upsert_artifact_bytes(wf_id=wf_id, tenant=tenant,
                                     path="/mount/old.png", data=PNG,
                                     content_type="image/png")
    ok = await repo.rename_artifact(wf_id=wf_id, tenant=tenant,
                                    old_path="/mount/old.png",
                                    new_path="/mount/new.png")
    assert ok is True
    assert await pg_session.get(VfsArtifact, (wf_id, "/mount/old.png")) is None
    moved = await repo.read_bytes(wf_id=wf_id, path="/mount/new.png")
    assert moved == PNG


@pytest.mark.asyncio
async def test_rename_file_to_same_path_is_non_destructive(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    await repo.upsert_artifact(
        wf_id=wf_id,
        tenant=tenant,
        path="/mount/unchanged.txt",
        content="keep me",
        content_type="text/plain",
    )

    ok = await repo.rename_artifact(
        wf_id=wf_id,
        tenant=tenant,
        old_path="/mount/unchanged.txt",
        new_path="/mount/unchanged.txt",
    )

    assert ok is True
    unchanged = await repo.read(
        wf_id=wf_id,
        path="/mount/unchanged.txt",
    )
    assert unchanged is not None
    assert unchanged.content == "keep me"


@pytest.mark.asyncio
async def test_rename_file_across_prefix(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    await repo.upsert_artifact(wf_id=wf_id, tenant=tenant,
                               path="/data/x.csv", content="a,b\n1,2",
                               content_type="table/csv")
    await repo.rename_artifact(wf_id=wf_id, tenant=tenant,
                               old_path="/data/x.csv", new_path="/mount/x.csv")
    assert await repo.read(wf_id=wf_id, path="/data/x.csv") is None
    e = await repo.read(wf_id=wf_id, path="/mount/x.csv")
    assert e is not None and e.content == "a,b\n1,2"


@pytest.mark.asyncio
async def test_rename_folder_rekeys_children(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    for p, c in (("/data/old/a.txt", "A"), ("/data/old/deep/b.txt", "B")):
        await repo.upsert_artifact(wf_id=wf_id, tenant=tenant, path=p, content=c)
    await repo.rename_artifact(wf_id=wf_id, tenant=tenant,
                               old_path="/data/old", new_path="/data/new")
    rows = {r.path for r in await repo.ls(wf_id=wf_id, prefix="/data/")}
    assert rows == {"/data/new/a.txt", "/data/new/deep/b.txt"}
    assert (await repo.read(wf_id=wf_id, path="/data/new/a.txt")).content == "A"
    assert (await repo.read(wf_id=wf_id, path="/data/new/deep/b.txt")).content == "B"


@pytest.mark.asyncio
async def test_rename_bad_new_path_raises(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    await repo.upsert_artifact(wf_id=wf_id, tenant=tenant, path="/data/x.csv", content="x")
    for bad in ("/etc/passwd", "/memory/x.md", "/data/../escape", "/data/"):
        with pytest.raises(ValueError):
            await repo.rename_artifact(wf_id=wf_id, tenant=tenant,
                                       old_path="/data/x.csv", new_path=bad)
    # original is untouched after a failed rename
    assert await repo.read(wf_id=wf_id, path="/data/x.csv") is not None


@pytest.mark.asyncio
async def test_rename_missing_source_raises(pg_session):
    tenant, wf_id = await _seed_pg(pg_session)
    repo = VfsRepo(pg_session, object_store=InMemoryObjectStore())
    with pytest.raises(ValueError):
        await repo.rename_artifact(wf_id=wf_id, tenant=tenant,
                                   old_path="/data/nope.csv",
                                   new_path="/data/dst.csv")


def test_route_path_validator_allowlist():
    """The route reuses `_validate_artifact_path` as the delete/rename
    boundary: durable user prefixes accepted, agent/system paths rejected."""
    from vibecanvas_api.routes.vfs import _validate_user_managed_path
    from fastapi import HTTPException

    assert _validate_user_managed_path("/data/x.csv") == "/data/x.csv"
    assert _validate_user_managed_path("/mount/sub/y.png") == "/mount/sub/y.png"
    assert _validate_user_managed_path("/data/sub") == "/data/sub"  # folder prefix
    for bad in ("/memory/x.md", "/etc/passwd", "/data/../x", "store/x", ""):
        with pytest.raises(HTTPException):
            _validate_user_managed_path(bad)
