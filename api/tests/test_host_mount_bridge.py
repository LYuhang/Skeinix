from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from vibecanvas_api.config import config
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.services.user_mount_workspace import (
    HostMountBridge,
    mount_scope_id,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.vfs_store import VfsRepo


@pytest.mark.asyncio
async def test_host_mount_bridge_syncs_create_update_rename_and_delete(
    client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"host-mount-{uuid.uuid4().hex[:8]}@example.com",
            "username": "host-mount-user",
            "password": "pw12345678",
        },
    )
    assert registration.status_code == 201, registration.text
    payload = registration.json()
    user_id = payload["user"]["user_id"]
    tenant_id = payload["session"]["active_organization_id"]

    mount_root = tmp_path / "host-mounts"
    mount_root.mkdir(mode=0o700)
    monkeypatch.setattr(config.storage, "mount_path", mount_root)
    bridge = HostMountBridge()
    directory = Path(
        bridge.register(tenant_id=tenant_id, user_id=user_id) or ""
    )
    assert directory == mount_root / "users" / user_id
    assert directory.stat().st_mode & 0o077 == 0

    source = directory / "reports" / "quarter.txt"
    source.parent.mkdir()
    source.write_text("from host", encoding="utf-8")
    assert await bridge.sync_user(tenant_id=tenant_id, user_id=user_id) == 1

    scope_id = mount_scope_id(user_id)
    async with session_scope(tenant_id=tenant_id) as session:
        repo = VfsRepo(session, object_store=get_object_store())
        assert await repo.read_bytes(
            wf_id=scope_id,
            path="/mount/reports/quarter.txt",
        ) == b"from host"
        await repo.upsert_artifact_bytes(
            wf_id=scope_id,
            tenant=tenant_id,
            path="/mount/reports/quarter.txt",
            data=b"from application",
            content_type="text/plain",
        )

    assert await bridge.sync_user(tenant_id=tenant_id, user_id=user_id) == 1
    assert source.read_bytes() == b"from application"

    renamed = directory / "reports" / "quarter-final.txt"
    source.rename(renamed)
    assert await bridge.sync_user(tenant_id=tenant_id, user_id=user_id) == 2
    async with session_scope(tenant_id=tenant_id) as session:
        repo = VfsRepo(session, object_store=get_object_store())
        assert await repo.read_bytes(
            wf_id=scope_id,
            path="/mount/reports/quarter.txt",
        ) is None
        assert await repo.read_bytes(
            wf_id=scope_id,
            path="/mount/reports/quarter-final.txt",
        ) == b"from application"

    renamed.unlink()
    assert await bridge.sync_user(tenant_id=tenant_id, user_id=user_id) == 1
    async with session_scope(tenant_id=tenant_id) as session:
        assert await VfsRepo(
            session,
            object_store=get_object_store(),
        ).read_bytes(
            wf_id=scope_id,
            path="/mount/reports/quarter-final.txt",
        ) is None


@pytest.mark.asyncio
async def test_host_mount_bridge_skips_symlink_ingress(
    client,
    tmp_path: Path,
    monkeypatch,
) -> None:
    registration = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"host-mount-link-{uuid.uuid4().hex[:8]}@example.com",
            "username": "host-mount-link-user",
            "password": "pw12345678",
        },
    )
    payload = registration.json()
    user_id = payload["user"]["user_id"]
    tenant_id = payload["session"]["active_organization_id"]
    mount_root = tmp_path / "host-mounts"
    mount_root.mkdir(mode=0o700)
    monkeypatch.setattr(config.storage, "mount_path", mount_root)
    bridge = HostMountBridge()
    directory = Path(bridge.register(tenant_id=tenant_id, user_id=user_id) or "")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not enter VFS", encoding="utf-8")
    (directory / "escape.txt").symlink_to(outside)

    assert await bridge.sync_user(tenant_id=tenant_id, user_id=user_id) == 0
    async with session_scope(tenant_id=tenant_id) as session:
        assert await VfsRepo(
            session,
            object_store=get_object_store(),
        ).read_bytes(
            wf_id=mount_scope_id(user_id),
            path="/mount/escape.txt",
        ) is None
