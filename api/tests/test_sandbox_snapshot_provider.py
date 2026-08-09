from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from vibecanvas_api.services.sandbox.gvisor import (
    RootfulGvisorProvider,
    RootlessGvisorProvider,
    ServeHandle,
    ServeSnapshot,
)


class _Process:
    pid = 12345

    def poll(self):
        return None

    def wait(self, timeout: float):
        return 0


def test_rootless_provider_rejects_checkpoint(tmp_path) -> None:
    provider = RootlessGvisorProvider("/runsc")
    handle = ServeHandle(_Process(), str(tmp_path), str(tmp_path), "worker")

    with pytest.raises(RuntimeError, match="rootless"):
        provider.checkpoint_serve(handle, image_dir=str(tmp_path / "image"))


def test_rootful_checkpoint_uses_profile_network_and_compression(
    monkeypatch, tmp_path,
) -> None:
    provider = RootfulGvisorProvider("/runsc")
    monkeypatch.setattr(
        provider,
        "_runtime_flags",
        lambda network, host_uds=False: ["/runsc", f"--network={network}"],
    )
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.gvisor.config.sandbox_snapshot_compression",
        "flate-best-speed",
    )
    observed: list[list[str]] = []

    def run(argv, **kwargs):
        observed.append(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("vibecanvas_api.services.sandbox.gvisor.subprocess.run", run)
    handle = ServeHandle(
        _Process(), str(tmp_path), str(tmp_path / "state"), "worker", network="none"
    )
    provider.checkpoint_serve(handle, image_dir=str(tmp_path / "image"), timeout=5)

    argv = observed[0]
    assert "--rootless" not in argv
    assert "--network=none" in argv
    assert "--compression=flate-best-speed" in argv
    assert "checkpoint" in argv


def test_checkpoint_does_not_kill_run_group_before_ordered_delete(
    monkeypatch, tmp_path,
) -> None:
    provider = RootfulGvisorProvider("/runsc")
    monkeypatch.setattr(
        provider,
        "_runtime_flags",
        lambda network, host_uds=False: ["/runsc", f"--network={network}"],
    )
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.gvisor.subprocess.run",
        lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.gvisor.os.killpg",
        lambda *_args: pytest.fail("checkpoint must leave teardown to stop_serve"),
    )
    handle = ServeHandle(
        _Process(), str(tmp_path), str(tmp_path / "state"), "worker", network="none"
    )
    provider.checkpoint_serve(handle, image_dir=str(tmp_path / "image"))


def test_rootful_restore_creates_then_restores_new_bundle(monkeypatch, tmp_path) -> None:
    provider = RootfulGvisorProvider("/runsc")
    image_dir = tmp_path / "image"
    image_dir.mkdir()
    (image_dir / "checkpoint.img").write_bytes(b"state")
    bundle = tmp_path / "bundle"
    state = bundle / "state"
    bundle.mkdir()
    state.mkdir()
    runs = tmp_path / "runs"
    work = tmp_path / "work"
    runs.mkdir()
    work.mkdir()
    monkeypatch.setattr(
        provider,
        "_runtime_flags",
        lambda network, host_uds=False: ["/runsc", f"--network={network or 'host'}"],
    )
    monkeypatch.setattr(
        provider,
        "_build_bundle",
        lambda **kwargs: (str(bundle), str(state), "restored-worker"),
    )
    calls: list[tuple[str, list[str]]] = []
    run_kwargs: list[dict] = []

    def run(argv, **kwargs):
        calls.append(("run", argv))
        run_kwargs.append(kwargs)
        if log_file := kwargs.get("stdout"):
            log_file.write("")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def popen(argv, **kwargs):
        calls.append(("popen", argv))
        return _Process()

    monkeypatch.setattr("vibecanvas_api.services.sandbox.gvisor.subprocess.run", run)
    monkeypatch.setattr("vibecanvas_api.services.sandbox.gvisor.subprocess.Popen", popen)

    handle = provider.restore_serve(
        snapshot=ServeSnapshot(str(image_dir), "fingerprint"),
        runs_root=str(runs),
        work_dir=str(work),
        command=["python", "serve"],
        network="none",
    )

    assert calls[0][0] == "run" and "create" in calls[0][1]
    assert calls[1][0] == "popen" and "restore" in calls[1][1]
    assert f"--image-path={image_dir}" in calls[1][1]
    assert handle.run_id == "restored-worker"
    assert handle.network == "none"
    assert run_kwargs[0]["stdout"] is not subprocess.PIPE
    assert run_kwargs[0]["stderr"] is subprocess.STDOUT
