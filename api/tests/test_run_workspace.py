"""Tests for the execution-local RunWorkspace owner."""
from __future__ import annotations

import threading

import pytest

import vibecanvas_api.services.run_workspace as workspace_module
from vibecanvas_api.services.run_workspace import RunWorkspace


@pytest.mark.asyncio
async def test_async_context_prepares_projects_and_releases(monkeypatch):
    calls = []

    def build(run_id, tenant_id):
        calls.append(("build", run_id, tenant_id, threading.get_ident()))
        return {"run_id": run_id, "run_dir": "/tmp/run"}

    async def project(run_id, tenant_id, run_dir, wf_id=None):
        calls.append(("project", run_id, tenant_id, run_dir, wf_id))
        return 1

    async def release(run_id, tenant_id, *, retain=False, keep_run=False):
        calls.append(("release", run_id, tenant_id, retain, keep_run))

    monkeypatch.setattr(workspace_module, "build_run_context", build)
    monkeypatch.setattr(workspace_module, "sync_run_back", project)
    monkeypatch.setattr(workspace_module, "release_run", release)

    async with RunWorkspace("run-1", "tenant", wf_id="workflow", keep_run=True) as workspace:
        assert workspace.run_context == {"run_id": "run-1", "run_dir": "/tmp/run"}

    assert calls[0][0] == "build"
    assert calls[0][3] != threading.get_ident()
    assert [item[0] for item in calls] == ["build", "project", "release"]


@pytest.mark.asyncio
async def test_transient_run_skips_result_projection(monkeypatch):
    calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_run_context",
        lambda run_id, tenant_id: {"run_id": run_id, "run_dir": "/tmp/run"},
    )

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("transient runs must not project files")

    async def release(run_id, tenant_id, *, retain=False, keep_run=False):
        calls.append((run_id, tenant_id, retain, keep_run))

    monkeypatch.setattr(workspace_module, "sync_run_back", unexpected)
    monkeypatch.setattr(workspace_module, "release_run", release)

    async with RunWorkspace("run-2", "tenant") as workspace:
        assert await workspace.sync_run() == 0

    assert calls == [("run-2", "tenant", False, False)]


@pytest.mark.asyncio
async def test_async_workspace_materializes_and_persists_user_mount(monkeypatch):
    calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_run_context",
        lambda run_id, tenant_id: {"run_id": run_id, "run_dir": "/tmp/run"},
    )

    async def create(*, user_id, tenant_id):
        calls.append(("create", user_id, tenant_id))
        return "/tmp/user-mount"

    async def persist(*, source, user_id, tenant_id):
        calls.append(("persist", source, user_id, tenant_id))
        return 1

    async def release(*_args, **_kwargs):
        return None

    monkeypatch.setattr(workspace_module, "create_user_mount", create)
    monkeypatch.setattr(workspace_module, "persist_user_mount", persist)
    monkeypatch.setattr(workspace_module, "remove_user_mount",
                        lambda path: calls.append(("remove", path)))
    monkeypatch.setattr(workspace_module, "release_run", release)

    async with RunWorkspace("run-mount", "tenant", user_id="user") as workspace:
        assert workspace.mount_dir == "/tmp/user-mount"

    assert calls == [
        ("create", "user", "tenant"),
        ("persist", "/tmp/user-mount", "user", "tenant"),
        ("remove", "/tmp/user-mount"),
    ]


def test_sync_context_uses_sync_release_and_projection(monkeypatch):
    calls = []
    def project(*args, **kwargs):
        calls.append(("project", args, kwargs))
        return 1

    monkeypatch.setattr(
        workspace_module,
        "build_run_context",
        lambda run_id, tenant_id: {"run_id": run_id, "run_dir": "/tmp/run"},
    )
    monkeypatch.setattr(workspace_module, "sync_run_back_sync", project)
    monkeypatch.setattr(
        workspace_module.PostgresVfsRunStore,
        "release_sync",
        lambda self, **kwargs: calls.append(("release", kwargs)),
    )

    with RunWorkspace("run-3", "tenant", wf_id="workflow", retain=True):
        pass

    assert [item[0] for item in calls] == ["project", "release"]


def test_sync_workspace_uses_sync_user_mount_facade(monkeypatch):
    calls = []
    monkeypatch.setattr(
        workspace_module,
        "build_run_context",
        lambda run_id, tenant_id: {"run_id": run_id, "run_dir": "/tmp/run"},
    )
    monkeypatch.setattr(
        workspace_module,
        "create_user_mount_sync",
        lambda **kwargs: calls.append(("create", kwargs)) or "/tmp/user-mount",
    )
    monkeypatch.setattr(
        workspace_module,
        "persist_user_mount_sync",
        lambda **kwargs: calls.append(("persist", kwargs)) or 1,
    )
    monkeypatch.setattr(
        workspace_module,
        "remove_user_mount",
        lambda path: calls.append(("remove", path)),
    )
    monkeypatch.setattr(
        workspace_module.PostgresVfsRunStore,
        "release_sync",
        lambda self, **kwargs: None,
    )

    with RunWorkspace("run-sync", "tenant", user_id="user") as workspace:
        assert workspace.mount_dir == "/tmp/user-mount"

    assert [call[0] for call in calls] == ["create", "persist", "remove"]
