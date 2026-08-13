from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from vibecanvas_api.services.sandbox import manager as manager_module
from vibecanvas_api.services.sandbox.manager import SandboxSession


class _FakeProcess:
    def poll(self):
        return None


class _ExitedProcess:
    def poll(self):
        return 1


class _NeverConnectedBroker:
    async def wait_connected(self) -> None:
        await asyncio.Event().wait()


class _FakeProvider:
    def __init__(self) -> None:
        self.launches = 0
        self.stops = 0
        self.launch_kwargs: list[dict] = []
        self.lifecycle: list[str] = []

    def launch_agent_runtime_bus(self, **kwargs):
        self.launches += 1
        self.launch_kwargs.append(kwargs)
        return SimpleNamespace(proc=_FakeProcess())

    def stop_run(self, _handle, *, kill: bool = False):
        assert kill is False
        self.stops += 1
        self.lifecycle.append("stop")


@pytest.mark.asyncio
async def test_runtime_restore_exit_fails_before_bus_timeout() -> None:
    started = time.monotonic()

    with pytest.raises(RuntimeError, match="agent_runtime_baseline_restore_exited"):
        await SandboxSession._wait_for_agent_runtime_connection(
            _NeverConnectedBroker(),
            SimpleNamespace(proc=_ExitedProcess()),
            timeout=30.0,
            restored_from_baseline=True,
        )

    assert time.monotonic() - started < 0.5


class _FakeBroker:
    def __init__(self, socket_path: str) -> None:
        self.socket_path = socket_path
        self.turn_id = ""
        self.closed = False

    async def start(self) -> None:
        return None

    async def wait_connected(self) -> None:
        return None

    def is_connected(self) -> bool:
        return not self.closed

    async def send(self, message: dict) -> None:
        self.turn_id = str((message.get("request") or {}).get("turn_id") or "")

    async def messages(self):
        yield {
            "type": "runtime_event",
            "event": {"type": "runtime.started", "turn_id": self.turn_id},
        }
        yield {"type": "runtime_result"}

    async def close(self) -> None:
        self.closed = True


class _ResetOnSecondSendBroker(_FakeBroker):
    instances: list["_ResetOnSecondSendBroker"] = []

    def __init__(self, socket_path: str) -> None:
        super().__init__(socket_path)
        self.send_count = 0
        self.__class__.instances.append(self)

    async def send(self, message: dict) -> None:
        self.send_count += 1
        if len(self.__class__.instances) == 1 and self.send_count == 2:
            raise ConnectionResetError("Connection lost")
        await super().send(message)


class _BlockingResultBroker(_FakeBroker):
    async def messages(self):
        yield {
            "type": "runtime_event",
            "event": {"type": "runtime.started", "turn_id": self.turn_id},
        }
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_main_runtime_process_is_reused_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _FakeBroker)
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        expose_run=False,
    )

    def request(turn_id: str) -> dict:
        return {"turn_id": turn_id, "runtime_type": "langchain"}

    first = [
        event async for event in session.run_agent_runtime_stream(request("turn-1"))
    ]
    second = [
        event async for event in session.run_agent_runtime_stream(request("turn-2"))
    ]

    assert first[0]["turn_id"] == "turn-1"
    assert second[0]["turn_id"] == "turn-2"
    assert provider.launches == 1
    assert provider.stops == 0
    assert provider.launch_kwargs[0]["env_overrides"][
        "AGENT_DEBUG_VIEW_ENABLED"
    ] in {"0", "1"}
    assert "allow_hosts" not in provider.launch_kwargs[0]
    await session.close()
    assert provider.stops == 1


@pytest.mark.asyncio
async def test_stale_runtime_transport_is_restored_before_user_turn_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    _ResetOnSecondSendBroker.instances = []
    monkeypatch.setattr(manager_module, "BusBroker", _ResetOnSecondSendBroker)
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        expose_run=False,
    )

    first = [
        event
        async for event in session.run_agent_runtime_stream(
            {"turn_id": "turn-1", "runtime_type": "langchain"}
        )
    ]
    second = [
        event
        async for event in session.run_agent_runtime_stream(
            {"turn_id": "turn-2", "runtime_type": "langchain"}
        )
    ]

    assert first[0]["turn_id"] == "turn-1"
    assert second[0]["turn_id"] == "turn-2"
    assert provider.launches == 2
    assert provider.stops == 1
    assert len(_ResetOnSecondSendBroker.instances) == 2
    await session.close()


@pytest.mark.asyncio
async def test_cancelled_turn_invalidates_runtime_before_next_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _BlockingResultBroker)
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        expose_run=False,
    )

    stream = session.run_agent_runtime_stream(
        {"turn_id": "turn-cancelled", "runtime_type": "langchain"}
    )
    assert (await anext(stream))["turn_id"] == "turn-cancelled"

    await stream.aclose()

    assert provider.launches == 1
    assert provider.stops == 1
    assert session._runtime_handle is None
    assert session._runtime_broker is None

    await session.close()


@pytest.mark.asyncio
async def test_runtime_is_reused_when_external_destination_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _FakeBroker)
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        expose_run=False,
    )

    def request(turn_id: str, base_url: str) -> dict:
        return {
            "turn_id": turn_id,
            "runtime_type": "langchain",
            "model": {"base_url": base_url},
        }

    first = [
        event
        async for event in session.run_agent_runtime_stream(
            request("turn-1", "https://model-a.example/v1")
        )
    ]
    second = [
        event
        async for event in session.run_agent_runtime_stream(
            request("turn-2", "https://model-b.example/v1")
        )
    ]

    assert first[0]["turn_id"] == "turn-1"
    assert second[0]["turn_id"] == "turn-2"
    assert provider.launches == 1
    assert provider.stops == 0
    assert "allow_hosts" not in provider.launch_kwargs[0]
    await session.close()
    assert provider.stops == 1


@pytest.mark.asyncio
async def test_codex_turn_uses_direct_runtime_volume_without_checkpointing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _FakeBroker)
    monkeypatch.setattr(
        manager_module,
        "resolve_codex_executable",
        lambda: "/bin/true",
    )
    monkeypatch.setattr(
        manager_module,
        "codex_cli_readonly_root",
        lambda _executable: "/bin",
    )
    monkeypatch.setattr(
        manager_module,
        "codex_cli_node_runtime",
        lambda _executable: None,
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat-scope",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        runtime_dir=str(runtime_dir),
        skills_dir=str(skills_dir),
        expose_run=False,
    )

    events = [
        event
        async for event in session.run_agent_runtime_stream(
            {
                "turn_id": "turn-1",
                "runtime_type": "codex",
                "model": {"connection_type": "managed_api"},
            }
        )
    ]

    assert events == [{"type": "runtime.started", "turn_id": "turn-1"}]
    auth_bind = dict(provider.launch_kwargs[0]["extra_rw_binds"])[
        "/runtime/.codex/auth.json"
    ]
    assert not auth_bind.startswith(str(runtime_dir))
    assert open(auth_bind, encoding="utf-8").read() == ""
    marker = runtime_dir / "context.txt"
    marker.write_text("durable without serialization", encoding="utf-8")
    await session.close()
    assert marker.read_text(encoding="utf-8") == "durable without serialization"
    assert provider.lifecycle == ["stop"]


@pytest.mark.asyncio
async def test_codex_account_runtime_is_reused_until_session_close(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _FakeBroker)
    monkeypatch.setattr(manager_module, "resolve_codex_executable", lambda: "/bin/true")
    monkeypatch.setattr(
        manager_module,
        "codex_cli_readonly_root",
        lambda _executable: "/bin",
    )
    monkeypatch.setattr(manager_module, "codex_cli_node_runtime", lambda _executable: None)
    auth_file = tmp_path / "account" / "auth.json"
    auth_file.parent.mkdir()
    auth_file.write_text('{"auth_mode":"chatgpt"}', encoding="utf-8")
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat-scope",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        runtime_dir=str(runtime_dir),
        account_auth_file=str(auth_file),
        expose_run=False,
    )

    def request(turn_id: str) -> dict:
        return {
            "turn_id": turn_id,
            "runtime_type": "codex",
            "model": {"connection_type": "chatgpt_account"},
        }

    first = [
        event async for event in session.run_agent_runtime_stream(request("turn-1"))
    ]
    second = [
        event async for event in session.run_agent_runtime_stream(request("turn-2"))
    ]

    assert first[0]["turn_id"] == "turn-1"
    assert second[0]["turn_id"] == "turn-2"
    assert provider.launches == 1
    assert provider.stops == 0
    assert session._runtime_uses_codex_account is True
    assert session.resource_status()["authentication"] == "account_bound"

    await session.close()
    assert provider.stops == 1


def test_codex_runtime_mounts_resolved_playwright_mcp_package(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    provider = _FakeProvider()
    package_root = tmp_path / "playwright-mcp"
    package_root.mkdir()
    launcher = package_root / "launch.cjs"
    launcher.write_text("#!/usr/bin/env node\n", encoding="utf-8")
    launcher.chmod(0o755)
    shim_root = tmp_path / "bin"
    shim_root.mkdir()
    shim = shim_root / "skeinix-playwright-mcp"
    shim.symlink_to(launcher)

    monkeypatch.setattr(manager_module, "resolve_codex_executable", lambda: "/bin/true")
    monkeypatch.setattr(
        manager_module,
        "codex_cli_readonly_root",
        lambda _executable: "/bin",
    )
    monkeypatch.setattr(manager_module, "codex_cli_node_runtime", lambda _path: None)
    monkeypatch.setenv("PLAYWRIGHT_MCP_COMMAND", str(shim))
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        expose_run=False,
    )

    _rw_binds, ro_binds, _env = session._agent_runtime_launch_spec(
        runtime_type="codex",
        uses_codex_account=False,
    )

    assert str(package_root) in ro_binds


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("runtime_type", "model", "expected_auth"),
    [
        ("langchain", {}, "detached"),
        ("codex", {"connection_type": "managed_api"}, "detached"),
        ("codex", {"connection_type": "chatgpt_account"}, "account_bound"),
    ],
)
async def test_all_interactive_runtimes_share_hibernate_security_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    runtime_type: str,
    model: dict,
    expected_auth: str,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _FakeBroker)
    monkeypatch.setattr(manager_module.config, "sandbox_resident_mode", "snapshot")
    monkeypatch.setattr(manager_module, "resolve_codex_executable", lambda: "/bin/true")
    monkeypatch.setattr(manager_module, "codex_cli_readonly_root", lambda _path: "/bin")
    monkeypatch.setattr(manager_module, "codex_cli_node_runtime", lambda _path: None)
    auth_file = tmp_path / "account" / "auth.json"
    auth_file.parent.mkdir()
    auth_file.write_text("{}", encoding="utf-8")
    session = SandboxSession(
        tenant_id="tenant",
        wf_id=f"chat-{runtime_type}-{model.get('connection_type', 'api')}",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        account_auth_file=str(auth_file),
        expose_run=False,
    )
    session.writeback_vfs = AsyncMock()

    events = [
        event
        async for event in session.run_agent_runtime_stream(
            {"turn_id": "turn-1", "runtime_type": runtime_type, "model": model}
        )
    ]
    assert events[0]["turn_id"] == "turn-1"
    assert session.resource_status()["authentication"] == expected_auth

    assert await session.hibernate() is True
    resources = session.resource_status()
    assert session._lifecycle_state == "hibernated"
    assert resources["runtime_type"] == runtime_type
    assert resources["runtime_process"] == "stopped"
    assert resources["authentication"] == "detached"
    assert resources["network"] == "disconnected"
    assert provider.stops == 1

    await session.close()


@pytest.mark.asyncio
async def test_runtime_binding_cannot_switch_inside_one_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _FakeProvider()
    monkeypatch.setattr(manager_module, "BusBroker", _FakeBroker)
    session = SandboxSession(
        tenant_id="tenant",
        wf_id="chat",
        run_dir=None,
        overlay_dir=None,
        provider=provider,
        base_binds=[],
        expose_run=False,
    )
    _ = [
        event
        async for event in session.run_agent_runtime_stream(
            {"turn_id": "turn-1", "runtime_type": "langchain"}
        )
    ]

    with pytest.raises(RuntimeError, match="sandbox_runtime_binding_mismatch"):
        _ = [
            event
            async for event in session.run_agent_runtime_stream(
                {"turn_id": "turn-2", "runtime_type": "codex", "model": {}}
            )
        ]

    await session.close()
