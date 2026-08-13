from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock

import httpx
import pytest
import websockets
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from vibecanvas_api.services.agent_runtime.codex_mcp_gateway import (
    CodexPlatformMcpGateway,
)
from vibecanvas_api.services.agent_runtime.protocol import RuntimeMcpServer


def _descriptor() -> RuntimeMcpServer:
    return RuntimeMcpServer(
        name="browser",
        source="platform",
        connection={
            "transport": "streamable_http",
            "url": "https://platform.test/api/internal/mcp/browser",
            "headers": {"Authorization": "Bearer private"},
        },
    )


def _stdio_descriptor() -> RuntimeMcpServer:
    server = r'''
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("stdio-gateway-test")
@mcp.tool()
def browser_snapshot(value: str = "") -> str:
    return "snapshot:" + value
mcp.run(transport="stdio")
'''
    return RuntimeMcpServer(
        name="browser",
        source="platform",
        connection={
            "transport": "stdio",
            "command": sys.executable,
            "args": ["-c", server],
        },
    )


def _success(name: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"{name} completed")],
        structuredContent={"status": "success", "tool": name},
        isError=False,
    )


@pytest.mark.asyncio
async def test_playwright_websocket_relay_is_loopback_authenticated_and_duplex(
    monkeypatch,
) -> None:
    bearer = "turn-scoped-browser-capability"
    observed_authorization: list[str] = []

    async def upstream(connection) -> None:
        observed_authorization.append(
            str(connection.request.headers.get("Authorization") or "")
        )
        async for message in connection:
            await connection.send(f"echo:{message}")

    upstream_server = await websockets.serve(upstream, "127.0.0.1", 0)
    upstream_port = int(upstream_server.sockets[0].getsockname()[1])
    monkeypatch.delenv("VC_RUNTIME_EGRESS_PROXY", raising=False)
    relay, relay_url, relay_state = (
        await CodexPlatformMcpGateway._start_playwright_websocket_relay(
            upstream_url=f"ws://127.0.0.1:{upstream_port}/cdp",
            expected_bearer=bearer,
        )
    )
    try:
        assert relay_url.startswith("ws://127.0.0.1:")
        async with websockets.connect(
            relay_url,
            additional_headers={"Authorization": f"Bearer {bearer}"},
        ) as client:
            await client.send("frame")
            assert await client.recv() == "echo:frame"
        assert observed_authorization == [f"Bearer {bearer}"]
        assert relay_state == {}

        async with websockets.connect(
            relay_url,
            additional_headers={"Authorization": "Bearer wrong"},
        ) as unauthorized:
            with pytest.raises(websockets.exceptions.ConnectionClosedError) as exc:
                await unauthorized.recv()
            assert exc.value.code == 4401
    finally:
        relay.close()
        await relay.wait_closed()
        upstream_server.close()
        await upstream_server.wait_closed()


@pytest.mark.asyncio
async def test_playwright_websocket_relay_reports_redacted_upstream_failure(
    monkeypatch,
) -> None:
    bearer = "turn-scoped-secret"
    monkeypatch.delenv("VC_RUNTIME_EGRESS_PROXY", raising=False)
    relay, relay_url, relay_state = (
        await CodexPlatformMcpGateway._start_playwright_websocket_relay(
            upstream_url="ws://127.0.0.1:1/cdp",
            expected_bearer=bearer,
        )
    )
    try:
        with pytest.raises(websockets.exceptions.ConnectionClosedError) as exc:
            async with websockets.connect(
                relay_url,
                additional_headers={"Authorization": f"Bearer {bearer}"},
            ) as client:
                await client.recv()
        assert exc.value.code == 1011
        # VPN/mirrored-network stacks may hold a closed loopback route in
        # SYN_SENT until the explicit handshake deadline instead of returning
        # ECONNREFUSED. Both are redacted upstream-unavailable diagnostics.
        assert relay_state["last_error"].startswith(
            ("ConnectionRefusedError:", "OSError:", "TimeoutError:")
        )
        assert bearer not in relay_state["last_error"]
    finally:
        relay.close()
        await relay.wait_closed()


@pytest.mark.asyncio
async def test_gateway_suspends_every_approval_candidate_for_host_policy() -> None:
    approvals: list[tuple[str, dict, str]] = []

    async def approve(name: str, args: dict, call_id: str) -> str:
        approvals.append((name, args, call_id))
        return "approve"

    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=approve,
    )
    gateway._forward_call = AsyncMock(return_value=_success("browser_click"))

    arguments = {"handle": "h1", "require_user_auth": False}
    result = await gateway.call_tool("browser_click", arguments)

    assert result.isError is False
    assert len(approvals) == 1
    gateway._forward_call.assert_awaited_once_with("browser_click", arguments)


@pytest.mark.asyncio
async def test_gateway_denial_is_structured_not_executed_and_never_forwards() -> None:
    async def deny(_name: str, _args: dict, _call_id: str) -> str:
        return "deny"

    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=deny,
    )
    gateway._forward_call = AsyncMock()

    result = await gateway.call_tool(
        "browser_type",
        {"handle": "input", "text": "value", "require_user_auth": True},
    )

    assert result.isError is True
    assert result.structuredContent["not_executed"] is True
    assert result.structuredContent["error"]["code"] == "user_denied"
    gateway._forward_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_keeps_read_only_browser_calls_out_of_hitl() -> None:
    approve = AsyncMock(return_value="approve")
    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=approve,
    )
    gateway._forward_call = AsyncMock(
        return_value=_success("browser_snapshot")
    )

    result = await gateway.call_tool("browser_snapshot", {})

    assert result.isError is False
    approve.assert_not_awaited()
    gateway._forward_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_obeys_turn_policy_when_approval_is_bypassed() -> None:
    approve = AsyncMock(return_value="approve")
    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=approve,
        requires_approval=lambda _name, _arguments: False,
    )
    gateway._forward_call = AsyncMock(return_value=_success("browser_click"))

    arguments = {"handle": "submit", "require_user_auth": True}
    result = await gateway.call_tool("browser_click", arguments)

    assert result.isError is False
    approve.assert_not_awaited()
    gateway._forward_call.assert_awaited_once_with("browser_click", arguments)


@pytest.mark.asyncio
async def test_gateway_rejects_unreviewed_playwright_tool_without_forwarding() -> None:
    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=AsyncMock(return_value="approve"),
    )
    gateway._forward_call = AsyncMock()

    result = await gateway.call_tool(
        "browser_run_code_unsafe", {"code": "async page => page.url()"}
    )

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "browser_tool_not_allowed"
    assert result.structuredContent["not_executed"] is True
    gateway._forward_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_resident_gateway_drops_turn_capability_while_idle() -> None:
    approve = AsyncMock(return_value="approve")
    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=approve,
    )
    gateway._forward_call = AsyncMock(return_value=_success("browser_click"))

    gateway.deactivate()
    result = await gateway.call_tool("browser_click", {"handle": "submit"})

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "runtime_turn_inactive"
    assert gateway._upstream_headers == {}
    approve.assert_not_awaited()
    gateway._forward_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_resident_gateway_rotates_private_turn_capability() -> None:
    first_approval = AsyncMock(return_value="approve")
    second_approval = AsyncMock(return_value="approve")
    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=first_approval,
    )
    gateway.deactivate()
    rotated = _descriptor().model_copy(deep=True)
    rotated.connection["headers"] = {"Authorization": "Bearer rotated"}

    gateway.activate(
        descriptor=rotated,
        request_approval=second_approval,
    )

    assert gateway._upstream_headers == {"Authorization": "Bearer rotated"}
    assert gateway._request_approval is second_approval


@pytest.mark.asyncio
async def test_gateway_exposes_standard_streamable_http_mcp(monkeypatch) -> None:
    tool = types.Tool(
        name="browser_click",
        description="Click one browser element.",
        inputSchema={
            "type": "object",
            "properties": {"handle": {"type": "string"}},
            "required": ["handle"],
        },
        annotations=types.ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )

    async def approve(_name: str, _args: dict, _call_id: str) -> str:
        return "approve"

    gateway = CodexPlatformMcpGateway(
        descriptor=_descriptor(),
        request_approval=approve,
    )
    monkeypatch.setattr(gateway, "_list_upstream_tools", AsyncMock(return_value=[tool]))
    monkeypatch.setattr(
        gateway,
        "_forward_call",
        AsyncMock(return_value=_success("browser_click")),
    )
    await gateway.start()
    try:
        assert gateway.url is not None
        # This is an in-process loopback protocol check. A developer machine's
        # HTTP(S)_PROXY must not intercept it.
        async with httpx.AsyncClient(
            follow_redirects=True,
            trust_env=False,
        ) as client:
            async with streamable_http_client(
                gateway.url, http_client=client
            ) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    assert [item.name for item in listed.tools] == ["browser_click"]
                    assert listed.tools[0].annotations.destructiveHint is True
                    result = await session.call_tool(
                        "browser_click", {"handle": "submit"}
                    )
        assert result.isError is False
        assert result.structuredContent == {
            "status": "success",
            "tool": "browser_click",
        }
    finally:
        await gateway.close()


@pytest.mark.asyncio
async def test_gateway_keeps_one_stdio_upstream_for_snapshot_and_action() -> None:
    gateway = CodexPlatformMcpGateway(
        descriptor=_stdio_descriptor(),
        request_approval=AsyncMock(return_value="approve"),
        requires_approval=lambda _name, _arguments: False,
    )
    await gateway.start()
    try:
        assert gateway.url is not None
        assert [tool.name for tool in gateway._tools] == ["browser_snapshot"]
        assert gateway._remote_owner_task is not None
        assert gateway._remote_owner_task is not asyncio.current_task()
        session = gateway._remote_session_instance
        result = await gateway.call_tool(
            "browser_snapshot", {"value": "same-session"}
        )
        assert gateway._remote_session_instance is session
        assert result.content[0].text == "snapshot:same-session"
    finally:
        await gateway.close()
