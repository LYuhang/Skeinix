from __future__ import annotations

import json
from pathlib import Path

import pytest
from vibecanvas_api.config import AppConfig, config
from vibecanvas_api.services.agent_runtime import checkpoint_store
from vibecanvas_api.services.agent_runtime.checkpoint_store import (
    LangChainCheckpointStore,
)
from vibecanvas_api.services.sandbox.gvisor import (
    RootlessGvisorProvider,
    _prepare_rootful_codex_auth_bind,
)


def test_runtime_database_url_is_a_host_only_override(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_DATABASE_URL", raising=False)
    cfg = AppConfig(
        {
            "database": {"url": "postgresql+asyncpg://app@db/product"},
            "agent_runtime_database_url": "postgresql://runtime@db/checkpoints",
        }
    )

    assert cfg.database.url.endswith("/product")
    assert cfg.agent_runtime_database_url.endswith("/checkpoints")


def test_runtime_database_urls_dynamically_fallback_to_product_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_DATABASE_URL", raising=False)
    cfg = AppConfig({"database": {"url": "postgresql+asyncpg://app@db/one"}})
    cfg.database.url = "postgresql+asyncpg://app@db/two"

    assert cfg.agent_runtime_database_url.endswith("/two")


@pytest.mark.asyncio
async def test_runtime_state_pool_only_probes_preprovisioned_schema(monkeypatch):
    statements: list[str] = []

    class FakeConnection:
        async def execute(self, statement: str):
            statements.append(statement)

    class FakeConnectionContext:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, exc_type, exc, traceback):
            return False

    class FakePool:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def open(self, **kwargs):
            return None

        def connection(self):
            return FakeConnectionContext()

        async def close(self):
            return None

    monkeypatch.setattr(checkpoint_store, "AsyncConnectionPool", FakePool)
    store = LangChainCheckpointStore("postgresql://runtime@db/checkpoints")

    await store._get_pool()

    assert statements == [
        "SELECT 1 FROM vc_runtime_checkpoints LIMIT 0",
        "SELECT 1 FROM vc_runtime_checkpoint_writes LIMIT 0",
    ]


def test_agent_runtime_bundle_has_no_database_or_kms_credentials(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(config, "sandbox_network", "none")
    monkeypatch.setenv("DATABASE_URL", "postgresql://platform:secret@db/product")
    monkeypatch.setenv(
        "AGENT_RUNTIME_DATABASE_URL", "postgresql://runtime:secret@db/checkpoints"
    )
    monkeypatch.setenv("KMS_LOCAL_MASTER_KEY", "must-not-cross-the-boundary")
    provider = RootlessGvisorProvider("/bin/true")
    handle = provider.launch_agent_runtime_bus(
            run_id="turn",
            bus_socket=str(tmp_path / "bus.sock"),
            tenant="tenant",
            extra_rw_binds=[("/data", str(tmp_path / "data"))],
        )
    try:
        oci = json.loads(
            Path(handle.bundle_dir, "config.json").read_text(encoding="utf-8")
        )
        environment = set(oci["process"]["env"])
        assert not any(item.startswith("DATABASE_URL=") for item in environment)
        assert not any(
            item.startswith("AGENT_RUNTIME_DATABASE_URL=") for item in environment
        )
        assert not any(item.startswith("KMS_") for item in environment)
    finally:
        provider.stop_run(handle, kill=True)


def test_agent_runtime_bundle_accepts_existing_writable_file_bind(tmp_path):
    """Account Codex mounts one auth file beneath the writable runtime volume."""
    auth_file = tmp_path / "account" / "auth.json"
    auth_file.parent.mkdir(parents=True)
    auth_file.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    auth_file.chmod(0o600)
    provider = RootlessGvisorProvider("/bin/true")

    handle = provider.launch_agent_runtime_bus(
        run_id="account-turn",
        bus_socket=str(tmp_path / "bus.sock"),
        tenant="tenant",
        extra_rw_binds=[
            ("/runtime", str(tmp_path / "runtime")),
            ("/runtime/.codex/auth.json", str(auth_file)),
        ],
    )
    try:
        oci = json.loads(
            Path(handle.bundle_dir, "config.json").read_text(encoding="utf-8")
        )
        auth_mount = next(
            mount
            for mount in oci["mounts"]
            if mount["destination"] == "/runtime/.codex/auth.json"
        )
        assert auth_mount["source"] == str(auth_file)
        assert "rw" in auth_mount["options"]
    finally:
        provider.stop_run(handle, kill=True)


def test_rootful_codex_auth_bind_grants_only_root_group_access(
    monkeypatch, tmp_path
):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    auth_file.chmod(0o600)
    calls: list[tuple[str, int, int]] = []

    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.gvisor.os.fchown",
        lambda _descriptor, owner, group: calls.append(("chown", owner, group)),
    )
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.gvisor.os.fchmod",
        lambda _descriptor, mode: calls.append(("chmod", mode, 0)),
    )

    _prepare_rootful_codex_auth_bind(str(auth_file))

    assert calls == [("chown", -1, 0), ("chmod", 0o660, 0)]


@pytest.mark.parametrize(
    ("egress_mode", "expected_network"),
    [("host-network", "host"), ("proxy", "none")],
)
def test_agent_runtime_network_is_independent_from_snapshot_worker(
    monkeypatch, tmp_path, egress_mode, expected_network
):
    """The live Runtime is stopped before the network-none worker is saved."""
    monkeypatch.setattr(config, "sandbox_network", "none")
    monkeypatch.setattr(config, "sandbox_egress_mode", egress_mode)
    provider = RootlessGvisorProvider("/bin/true")
    observed: dict[str, object] = {}

    def runtime_flags(network, *, host_uds=False):
        observed.update(network=network, host_uds=host_uds)
        return ["/bin/true"]

    monkeypatch.setattr(provider, "_runtime_flags", runtime_flags)
    handle = provider.launch_agent_runtime_bus(
        run_id="runtime-network",
        bus_socket=str(tmp_path / "bus.sock"),
        tenant="tenant",
        extra_rw_binds=[("/data", str(tmp_path / "data"))],
    )
    try:
        assert observed == {"network": expected_network, "host_uds": True}
        oci = json.loads(
            Path(handle.bundle_dir, "config.json").read_text(encoding="utf-8")
        )
        environment = set(oci["process"]["env"])
        destinations = {mount["destination"] for mount in oci["mounts"]}
        if egress_mode == "proxy":
            assert "HTTP_PROXY=http://127.0.0.1:13128" in environment
            assert "HTTPS_PROXY=http://127.0.0.1:13128" in environment
            assert "NO_PROXY=127.0.0.1,localhost" in environment
            assert "/vcegress" in destinations
            assert handle.egress_loop_thread is not None
        else:
            assert not any(item.startswith("HTTP_PROXY=") for item in environment)
            assert "/vcegress" not in destinations
            assert handle.egress_loop_thread is None
    finally:
        provider.stop_run(handle, kill=True)
