from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from vibecanvas_api.services.agent_runtime.mcp import (
    _clear_runtime_mcp_tool_cache,
    load_runtime_mcp_tools,
    platform_mcp_descriptors,
    platform_mcp_names_for_modes,
)
from vibecanvas_api.services.agent_runtime.protocol import RuntimeMcpServer
from vibecanvas_api.services.platform_mcp.capability import (
    verify_platform_mcp_capability,
)
from vibecanvas_api.config import config


def test_platform_mcp_selection_is_command_driven_and_stable() -> None:
    base = ["config", "interactive", "workflow"]
    assert platform_mcp_names_for_modes([]) == base
    assert platform_mcp_names_for_modes([], runtime_type="langchain") == base
    assert platform_mcp_names_for_modes([], runtime_type="codex") == base
    assert platform_mcp_names_for_modes(["build"]) == [*base, "build"]
    assert platform_mcp_names_for_modes(["browser"]) == [*base, "browser"]
    assert platform_mcp_names_for_modes(["diagram"]) == [*base, "diagram"]
    assert platform_mcp_names_for_modes(
        ["diagram"], runtime_type="langchain"
    ) == platform_mcp_names_for_modes(
        ["diagram"], runtime_type="codex"
    )
    assert platform_mcp_names_for_modes(["browser", "build"]) == [
        *base,
        "build",
        "browser",
    ]
    assert platform_mcp_names_for_modes(
        ["knowledge", "deployment", "task"]
    ) == [*base, "task", "deployment", "knowledge"]
    assert platform_mcp_names_for_modes(
        ["browser", "build"],
        runtime_type="langchain",
    ) == [*base, "build", "browser"]


def test_platform_mcp_authorization_ceiling_is_runtime_neutral() -> None:
    common = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "user_id": "22222222-2222-2222-2222-222222222222",
        "chat_id": "chat-runtime-neutral",
        "turn_id": "turn-runtime-neutral",
        "workspace_scope_id": "workspace-runtime-neutral",
        "session_id": "33333333-3333-3333-3333-333333333333",
        "session_generation": 4,
        "membership_id": "44444444-4444-4444-4444-444444444444",
    }
    descriptors = [
        platform_mcp_descriptors(
            ["workflow"],
            runtime_session_id=runtime_session_id,
            **common,
        )[0]
        for runtime_session_id in ("runtime-langchain", "runtime-codex")
    ]
    capabilities = [
        verify_platform_mcp_capability(
            descriptor.connection["headers"]["Authorization"].removeprefix(
                "Bearer "
            ),
            secret=config.signing_secret,
            server="workflow",
        )
        for descriptor in descriptors
    ]
    assert all(capability is not None for capability in capabilities)
    langchain, codex = capabilities
    assert langchain is not None and codex is not None
    assert langchain.resources == codex.resources
    assert langchain.actions == codex.actions
    assert langchain.authorization_generation == codex.authorization_generation
    assert langchain.runtime_session_id == "runtime-langchain"
    assert codex.runtime_session_id == "runtime-codex"


def test_runtime_mcp_descriptor_accepts_standard_connections() -> None:
    stdio = RuntimeMcpServer(
        name="files",
        source="custom",
        connection={"transport": "stdio", "command": "mcp-files", "args": ["/data"]},
    )
    remote = RuntimeMcpServer(
        name="github",
        source="custom",
        connection={
            "transport": "streamable-http",
            "url": "https://example.test/mcp",
            "headers": {"Authorization": "Bearer secret"},
        },
    )

    assert stdio.connection["transport"] == "stdio"
    assert remote.connection["transport"] == "streamable_http"


@pytest.mark.parametrize(
    "connection",
    [
        {"transport": "stdio", "args": []},
        {"transport": "websocket", "url": "wss://example.test/mcp"},
        {"transport": "streamable_http", "url": "/relative/mcp"},
        {
            "transport": "streamable_http",
            "url": "https://example.test/mcp",
            "headers": {"X-Test": 1},
        },
    ],
)
def test_runtime_mcp_descriptor_rejects_invalid_connections(connection: dict) -> None:
    with pytest.raises(ValidationError):
        RuntimeMcpServer(name="bad", source="custom", connection=connection)


@pytest.mark.asyncio
async def test_runtime_loader_uses_official_adapter_and_qualifies_names(monkeypatch) -> None:
    _clear_runtime_mcp_tool_cache()
    calls: list[tuple[str, dict]] = []

    class FakeClient:
        def __init__(self, connections, **kwargs):
            calls.append(("init", {"connections": connections, **kwargs}))

        async def get_tools(self, *, server_name=None):
            calls.append(("list", {"server_name": server_name}))
            return [SimpleNamespace(name="search")]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient
    )
    server = RuntimeMcpServer(
        name="docs",
        source="custom",
        connection={"transport": "stdio", "command": "mcp-docs", "args": []},
    )

    tools, catalog = await load_runtime_mcp_tools([server])

    assert [tool.name for tool in tools] == ["docs__search"]
    assert catalog[0] == {
        "name": "docs",
        "server_id": None,
        "description": "",
        "loaded": True,
        "source": "custom",
        "tool_count": 1,
        "health": "ready",
        "cache_status": "miss",
        "handshake_ms": catalog[0]["handshake_ms"],
        "retry_count": 0,
        "config_revision": None,
        "tools": [
            {
                "id": "custom:docs:docs__search",
                "name": "docs__search",
                "description": "",
                "origin": "custom_mcp",
                "capability": "docs",
                "risk": "unknown",
                "load_policy": "always",
                "required_policy": "optional",
                "runtime_compatibility": ["langchain", "codex"],
                "input_schema": None,
                "version": "unversioned",
            }
        ],
    }
    assert catalog[0]["handshake_ms"] >= 0
    assert calls[0][1]["connections"] == {"docs": server.connection}
    assert calls[0][1]["handle_tool_errors"] is True
    assert calls[1] == ("list", {"server_name": "docs"})


@pytest.mark.asyncio
async def test_runtime_loader_reuses_secretless_stdio_cache_by_revision(monkeypatch) -> None:
    _clear_runtime_mcp_tool_cache()
    calls = 0

    class FakeClient:
        def __init__(self, _connections, **_kwargs):
            pass

        async def get_tools(self, *, server_name=None):
            nonlocal calls
            calls += 1
            return [SimpleNamespace(name="search")]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", FakeClient
    )
    connection = {"transport": "stdio", "command": "mcp-docs", "args": []}
    first = RuntimeMcpServer(
        name="docs",
        source="custom",
        server_id="server-1",
        config_revision="revision-1",
        connection=connection,
    )
    changed = first.model_copy(update={"config_revision": "revision-2"})

    first_tools, first_catalog = await load_runtime_mcp_tools([first])
    cached_tools, cached_catalog = await load_runtime_mcp_tools([first])
    changed_tools, changed_catalog = await load_runtime_mcp_tools([changed])

    assert [tool.name for tool in first_tools] == ["docs__search"]
    assert [tool.name for tool in cached_tools] == ["docs__search"]
    assert [tool.name for tool in changed_tools] == ["docs__search"]
    assert calls == 2
    assert first_catalog[0]["cache_status"] == "miss"
    assert cached_catalog[0]["cache_status"] == "hit"
    assert changed_catalog[0]["cache_status"] == "miss"


@pytest.mark.asyncio
async def test_runtime_loader_retries_one_transient_handshake(monkeypatch) -> None:
    _clear_runtime_mcp_tool_cache()
    calls = 0

    class FlakyClient:
        def __init__(self, _connections, **_kwargs):
            pass

        async def get_tools(self, *, server_name=None):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("temporary reset")
            return [SimpleNamespace(name="search")]

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", FlakyClient
    )
    tools, catalog = await load_runtime_mcp_tools([
        RuntimeMcpServer(
            name="docs",
            source="custom",
            connection={"transport": "stdio", "command": "mcp-docs", "args": []},
        )
    ])

    assert [tool.name for tool in tools] == ["docs__search"]
    assert calls == 2
    assert catalog[0]["retry_count"] == 1
    assert catalog[0]["health"] == "ready"


@pytest.mark.asyncio
async def test_runtime_loader_isolates_custom_failure_but_closes_platform(monkeypatch) -> None:
    class BrokenClient:
        def __init__(self, *_args, **_kwargs):
            pass

        async def get_tools(self, *, server_name=None):
            raise OSError(f"cannot reach {server_name}")

    monkeypatch.setattr(
        "langchain_mcp_adapters.client.MultiServerMCPClient", BrokenClient
    )
    connection = {"transport": "stdio", "command": "missing", "args": []}

    tools, catalog = await load_runtime_mcp_tools(
        [RuntimeMcpServer(name="optional", source="custom", connection=connection)]
    )
    assert tools == []
    assert catalog[0]["loaded"] is False
    assert "cannot reach optional" in catalog[0]["error"]

    with pytest.raises(RuntimeError, match="platform MCP workflow could not be loaded"):
        await load_runtime_mcp_tools(
            [RuntimeMcpServer(name="workflow", source="platform", connection=connection)]
        )
