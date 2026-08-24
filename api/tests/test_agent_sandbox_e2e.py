# -*- coding: utf-8 -*-
"""F0F2-T6 — @gvisor END-TO-END: a RESIDENT Agent sandbox session runs a real
Python Skill script INSIDE rootless gVisor and the whole data-path holds:

  * the user's persistent ``/mount`` is hydrated and bound into the sandbox, so
    the script reads a seeded ``/mount/in.txt``;
  * the script WRITES ``/logs/out.txt`` through the clean top-level workspace
    folder, and ``SandboxSession.writeback_vfs`` then
    surfaces it in the durable VFS at ``/logs/out.txt`` (read back here);
  * NETWORK-NONE IS ENFORCED — an explicit ``network="none"`` capability probe
    cannot create an outbound socket. Normal Agent code uses the deployment's
    unified egress policy (proxy in the production Compose profile), so this
    test must not accidentally assert that every code job is offline.

Skip-clean when rootless gVisor is unavailable, MIRRORING the sibling @gvisor
tests' guard (``pytest.mark.skipif(not _gvisor_runnable())``) — the conftest
``pytest_configure`` hook fetches a pinned ``runsc`` so this RUNS where gVisor
can boot and SKIPS cleanly where it cannot.

The VFS seed / readback uses the SAME helpers the other VFS tests use
(``tests.test_vfs_store._seed_pg`` + ``VfsRepo.upsert_artifact_bytes`` /
``read_bytes``), driven through a COMMITTED ``session_scope`` so the manager's
own short-lived sessions (a separate transaction) can see the seeded rows. The
object store is pointed at a real ``FilesystemObjectStore`` (NOT InMemory) so
``build_run_context`` can materialize a real ``run_dir`` — InMemory degrades
``run_dir`` to ``None`` and ``/logs`` write-back would have nowhere to read from.
"""
from __future__ import annotations

import base64
import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.config import config as _config
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.services.sandbox import _gvisor_runnable
from vibecanvas_api.services.sandbox.manager import get_sandbox_manager
from vibecanvas_api.services.user_mount_workspace import mount_scope_id
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.sync_session import current_sync_tenant_id
from vibecanvas_api.storage.vfs_store import VfsRepo
from vibecanvas_api.storage.workflow_repo import WorkflowRepo

# Mirror the sibling @gvisor guard exactly (test_sandbox_gvisor.py /
# test_sandbox_workflow_acceptance.py): skip cleanly if rootless gVisor can't
# boot here. The ``gvisor`` marker is ALSO applied so ``pytest -m gvisor``
# selects this test.
gvisor = pytest.mark.skipif(
    not _gvisor_runnable(), reason="rootless gVisor not runnable here")


@pytest.fixture
def _fs_object_store(monkeypatch, tmp_path):
    """Point the configured object store + staging/overlay roots at real
    tmp_path dirs so ``build_run_context`` materializes a REAL ``run_dir``
    (InMemory degrades it to ``None`` and the ``/logs`` write-back would have
    no host dir to read). Returns nothing — it just reconfigures the singleton
    config the manager + VFS facades read.
    """
    monkeypatch.setattr(_config.object_store, "provider", "filesystem")
    monkeypatch.setattr(_config.object_store, "fs_root", str(tmp_path / "objstore"))
    monkeypatch.setattr(_config, "agent_overlay_root", str(tmp_path / "overlay"))
    monkeypatch.setattr(_config, "kms_provider", "local")
    monkeypatch.setattr(
        _config,
        "kms_local_master_key",
        base64.urlsafe_b64encode(b"s" * 32).decode(),
    )
    monkeypatch.setattr(_config, "kms_local_master_key_file", "")


async def _seed_committed_workflow_with_mount_file(rel: str, data: bytes):
    """Seed tenant + user + workflow and one user ``/mount/<rel>`` file via a
    COMMITTED ``session_scope`` (NOT the rolled-back ``pg_session``), so the
    manager's own separate transaction sees the rows. Returns ``(tenant_hex,
    user_id, wf_id)``. Mirrors ``tests.test_vfs_store._seed_pg`` and writes the
    ``VfsRepo.upsert_artifact_bytes`` helper the other VFS tests use.
    """
    t, u = uuid.uuid4(), uuid.uuid4()
    tenant_hex = t.hex
    async with session_scope(tenant_id=str(t)) as s:
        # auth tables (tenants/users) have no RLS; the GUC is set so the
        # workflow INSERT's tenant_id DEFAULT + FORCE RLS resolve.
        await s.execute(text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'x')"),
                        {"t": t})
        await s.execute(
            text("INSERT INTO users(user_id,tenant_id,email) VALUES (:u,:t,:e)"),
            {"u": u, "t": t, "e": f"{u.hex[:6]}@example.com"})
        wf = await WorkflowRepo(s, str(u)).create_workflow(name="W")
        wf_id = wf["wf_id"]
        repo = VfsRepo(s, object_store=get_object_store())
        user_mount_scope_id = mount_scope_id(str(u))
        await repo.upsert_artifact_bytes(
            wf_id=user_mount_scope_id, tenant=tenant_hex,
            path=f"/mount/{rel}", data=data,
            content_type="text/plain")
    return tenant_hex, str(u), wf_id


# The Skill script: reads inputs JSON from stdin (the engine code-job pipes
# job.json's ``inputs`` to stdin), reads the seeded user file, writes
# /logs/out.txt (the clean workspace folder written back to VFS /logs/), and
# prints a marker.
_SCRIPT = """
import json, os, sys
inp = json.load(sys.stdin)
with open("/mount/in.txt", "rb") as f:
    mount_content = f.read().decode()
os.makedirs("/logs", exist_ok=True)
out = "n=%d mount=%s" % (inp["n"], mount_content)
with open("/logs/out.txt", "w", encoding="utf-8") as f:
    f.write(out)
print("MARKER_OK " + out)
"""

# A second script that PROVES network is off: any outbound connection must fail.
_NET_SCRIPT = """
import socket, sys
try:
    socket.create_connection(("8.8.8.8", 53), timeout=3)
    print("NET_REACHED")           # must NOT happen — egress is blocked
except Exception as exc:
    print("NET_BLOCKED: %r" % (exc,), file=sys.stderr)
    sys.exit(3)
"""

_MOUNT_WRITE_SCRIPT = """
with open("/mount/in.txt", "r", encoding="utf-8") as f:
    original = f.read()
with open("/mount/generated.txt", "w", encoding="utf-8") as f:
    f.write(original + "-persisted")
print("MOUNT_WRITTEN")
"""

_MOUNT_READ_SCRIPT = """
with open("/mount/generated.txt", "r", encoding="utf-8") as f:
    print("MOUNT_REHYDRATED " + f.read())
"""


@gvisor
@pytest.mark.gvisor
@pytest.mark.asyncio
async def test_resident_session_reads_mount_writes_logs(_fs_object_store):
    tenant_id, user_id, wf_id = await _seed_committed_workflow_with_mount_file(
        "in.txt", b"hello-mount")

    # Short-session hydration reads the tenant from this ContextVar.
    # agent.run_agent_turn.
    token = current_sync_tenant_id.set(tenant_id)
    try:
        mgr = get_sandbox_manager()
        session = await mgr.get_session(tenant_id, wf_id, user_id=user_id)
        result = await session.run_code(_SCRIPT, {"n": 7}, timeout_s=60)
    finally:
        current_sync_tenant_id.reset(token)

    assert result["exit_code"] == 0, (
        f"exit={result['exit_code']} error={result['error']} "
        f"stderr={result['stderr'][-800:]}")
    # marker + the mounted content both surfaced in stdout
    assert "MARKER_OK" in result["stdout"], result["stdout"]
    assert "hello-mount" in result["stdout"], result["stdout"]
    assert "n=7" in result["stdout"], result["stdout"]

    # the run-dir /logs file was written back to the durable VFS at /logs/out.txt
    async with session_scope(tenant_id=tenant_id) as s:
        repo = VfsRepo(s, object_store=get_object_store())
        body = await repo.read_bytes(wf_id=wf_id, path="/logs/out.txt")
    assert body is not None, "/logs/out.txt did not surface in the VFS"
    assert body.decode() == "n=7 mount=hello-mount", body


@gvisor
@pytest.mark.gvisor
@pytest.mark.asyncio
async def test_resident_session_network_is_off(_fs_object_store):
    tenant_id, user_id, wf_id = await _seed_committed_workflow_with_mount_file(
        "in.txt", b"hello-mount")

    token = current_sync_tenant_id.set(tenant_id)
    try:
        mgr = get_sandbox_manager()
        session = await mgr.get_session(tenant_id, wf_id, user_id=user_id)
        result = await session.run_code(
            _NET_SCRIPT,
            {},
            timeout_s=60,
            network="none",
        )
    finally:
        current_sync_tenant_id.reset(token)

    # network=none ⇒ the connection attempt raised inside the sandbox: non-zero
    # exit + the block message in stderr, and the "reached" marker is ABSENT.
    assert result["exit_code"] != 0, (
        f"egress was NOT blocked: exit={result['exit_code']} "
        f"stdout={result['stdout']!r}")
    assert "NET_REACHED" not in result["stdout"], result["stdout"]
    assert "NET_BLOCKED" in result["stderr"], result["stderr"]


@gvisor
@pytest.mark.gvisor
@pytest.mark.asyncio
async def test_mount_writeback_survives_release_and_cross_workflow_rehydrate(
    _fs_object_store,
):
    """A real sandbox write is durable and visible to a new Workflow session."""
    tenant_id, user_id, first_wf_id = (
        await _seed_committed_workflow_with_mount_file("in.txt", b"shared")
    )
    async with session_scope(tenant_id=tenant_id) as session:
        second = await WorkflowRepo(session, user_id).create_workflow(name="W2")
        second_wf_id = second["wf_id"]

    manager = get_sandbox_manager()
    token = current_sync_tenant_id.set(tenant_id)
    try:
        first_session = await manager.get_session(
            tenant_id, first_wf_id, user_id=user_id
        )
        written = await first_session.run_code(
            _MOUNT_WRITE_SCRIPT, {}, timeout_s=60
        )
        assert written["exit_code"] == 0, written

        # The turn-end writeback must be queryable before the sandbox is closed.
        async with session_scope(tenant_id=tenant_id) as session:
            repo = VfsRepo(session, object_store=get_object_store())
            persisted = await repo.read_bytes(
                wf_id=mount_scope_id(user_id), path="/mount/generated.txt"
            )
        assert persisted == b"shared-persisted"

        # Release the first session, then hydrate a distinct Workflow session.
        await manager.close_session(tenant_id, first_wf_id)
        await manager.drain_background_closes()
        second_session = await manager.get_session(
            tenant_id, second_wf_id, user_id=user_id
        )
        rehydrated = await second_session.run_code(
            _MOUNT_READ_SCRIPT, {}, timeout_s=60
        )
        assert rehydrated["exit_code"] == 0, rehydrated
        assert "MOUNT_REHYDRATED shared-persisted" in rehydrated["stdout"]
    finally:
        await manager.close_session(tenant_id, first_wf_id)
        await manager.close_session(tenant_id, second_wf_id)
        await manager.drain_background_closes()
        current_sync_tenant_id.reset(token)
