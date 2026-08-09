from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
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


def _success(name: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=f"{name} completed")],
        structuredContent={"status": "success", "tool": name},
        isError=False,
    )


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
        return_value=_success("browser_read_text")
    )

    result = await gateway.call_tool("browser_read_text", {"handle": "body"})

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
