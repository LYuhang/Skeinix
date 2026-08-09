"""Turn-local Platform MCP policy gateway for the Codex sandbox Runtime.

Codex owns its native agent loop, while Platform MCP capabilities and product
HITL must remain private to the trusted Python Runtime around it. This gateway
keeps that control point inside the Chat sandbox while preserving standard MCP
on both sides:

    Codex MCP client -> localhost Streamable HTTP gateway -> Platform MCP

The upstream capability header remains private to this process.  The model and
Codex app-server only receive the loopback URL.  Approval decisions arrive over
the existing host<->sandbox Runtime control protocol and are therefore durable
in the backend before this gateway observes them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

from vibecanvas_api.services.agent_runtime.approval import (
    is_pre_tool_approval_candidate,
)
from vibecanvas_api.services.agent_runtime.protocol import RuntimeMcpServer

ApprovalCallback = Callable[
    [str, dict[str, Any], str],
    Awaitable[str],
]
ApprovalPredicate = Callable[[str, dict[str, Any]], bool]


def _not_executed_result(tool_name: str, action: str) -> types.CallToolResult:
    reason = "user_cancelled" if action == "cancel" else "user_denied"
    message = "Tool call was not executed because user approval was not granted."
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        structuredContent={
            "status": "error",
            "error": {"code": reason, "message": message},
            "tool": tool_name,
            "not_executed": True,
        },
        isError=True,
    )


def _inactive_result(tool_name: str) -> types.CallToolResult:
    message = "The Platform MCP capability is not active for an Agent Turn."
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        structuredContent={
            "status": "error",
            "error": {"code": "runtime_turn_inactive", "message": message},
            "tool": tool_name,
            "not_executed": True,
        },
        isError=True,
    )


class CodexPlatformMcpGateway:
    """Proxy one privileged Platform MCP through the Runtime approval gate."""

    def __init__(
        self,
        *,
        descriptor: RuntimeMcpServer,
        request_approval: ApprovalCallback,
        requires_approval: ApprovalPredicate | None = None,
    ) -> None:
        if descriptor.source != "platform":
            raise ValueError("Codex Platform MCP gateway requires a platform descriptor")
        connection = dict(descriptor.connection)
        if connection.get("transport") != "streamable_http":
            raise ValueError("Codex Platform MCP gateway requires Streamable HTTP")
        self.descriptor = descriptor
        self._request_approval: ApprovalCallback | None = None
        self._requires_approval: ApprovalPredicate | None = None
        self._upstream_url = ""
        self._upstream_headers: dict[str, str] = {}
        self._active = False
        self._tools: list[types.Tool] = []
        self._mcp: FastMCP | None = None
        self._server: uvicorn.Server | None = None
        self._server_task: asyncio.Task[None] | None = None
        self._session_manager_context: Any = None
        self._socket: socket.socket | None = None
        self.url: str | None = None
        self.activate(
            descriptor=descriptor,
            request_approval=request_approval,
            requires_approval=requires_approval,
        )

    def activate(
        self,
        *,
        descriptor: RuntimeMcpServer,
        request_approval: ApprovalCallback,
        requires_approval: ApprovalPredicate | None = None,
    ) -> None:
        """Install only the current Turn's private upstream capability.

        The loopback MCP endpoint remains stable for the resident Codex
        app-server, but its upstream Authorization header and approval callback
        are replaced before every Turn. Turns are serialized by SandboxSession.
        """
        if descriptor.source != "platform":
            raise ValueError("Codex Platform MCP gateway requires a platform descriptor")
        if self.url is not None and descriptor.name != self.descriptor.name:
            raise ValueError("resident Platform MCP gateway name cannot change")
        connection = dict(descriptor.connection)
        if connection.get("transport") != "streamable_http":
            raise ValueError("Codex Platform MCP gateway requires Streamable HTTP")
        self.descriptor = descriptor
        self._upstream_url = str(connection["url"])
        self._upstream_headers = dict(connection.get("headers") or {})
        self._request_approval = request_approval
        self._requires_approval = requires_approval or (
            lambda name, arguments: is_pre_tool_approval_candidate(name, arguments)
        )
        self._active = True

    def deactivate(self) -> None:
        """Drop all Turn-private authority while keeping loopback MCP warm."""
        self._active = False
        self._upstream_headers = {}
        self._request_approval = None
        self._requires_approval = None

    async def _remote_session(self, stack: contextlib.AsyncExitStack) -> ClientSession:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=self._upstream_headers,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, read=None),
                # Platform MCP is an operator-configured private service hop.
                # Never let an ambient developer/host proxy intercept its
                # short-lived Chat/Turn capability header.
                trust_env=False,
                # In the network-none production profile this explicit,
                # launcher-owned proxy is the only path to the private Platform
                # MCP service. Ambient host proxy variables are still ignored.
                proxy=os.environ.get("VC_RUNTIME_EGRESS_PROXY") or None,
            )
        )
        streams = await stack.enter_async_context(
            streamable_http_client(self._upstream_url, http_client=client)
        )
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    async def _list_upstream_tools(self) -> list[types.Tool]:
        async with contextlib.AsyncExitStack() as stack:
            session = await self._remote_session(stack)
            result = await session.list_tools()
            return list(result.tools)

    async def _forward_call(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        async with contextlib.AsyncExitStack() as stack:
            session = await self._remote_session(stack)
            return await session.call_tool(name, arguments)

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """Apply product policy, then forward the original CallTool at most once."""
        if not self._active:
            return _inactive_result(name)
        call_id = f"mcp_{uuid.uuid4().hex}"
        requires_approval = self._requires_approval
        request_approval = self._request_approval
        if requires_approval is None or request_approval is None:
            return _inactive_result(name)
        if requires_approval(name, arguments):
            action = await request_approval(name, dict(arguments), call_id)
            if action != "approve":
                return _not_executed_result(name, action)
        return await self._forward_call(name, arguments)

    async def start(self) -> None:
        if self._server_task is not None:
            return
        self._tools = await self._list_upstream_tools()
        mcp = FastMCP(
            f"vibecanvas-{self.descriptor.name}-runtime-gateway",
            instructions=(
                "Skeinix platform capability. Calls are scoped to the current "
                "Chat and active Agent Turn."
            ),
            streamable_http_path="/",
            stateless_http=True,
            json_response=False,
        )
        lowlevel = mcp._mcp_server

        @lowlevel.list_tools()
        async def list_tools() -> list[types.Tool]:
            # Preserve the official upstream schemas and annotations. Codex's
            # own MCP prompt is disabled for this server in _mcp_config because
            # the gateway is the authoritative, argument-aware product gate.
            return list(self._tools)

        @lowlevel.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]):
            return await self.call_tool(name, arguments)

        app = mcp.streamable_http_app()
        session_context = mcp.session_manager.run()
        await session_context.__aenter__()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        sock.setblocking(False)
        port = int(sock.getsockname()[1])
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                log_config=None,
                access_log=False,
                lifespan="off",
            )
        )
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            for _ in range(200):
                if server.started:
                    break
                if task.done():
                    await task
                    raise RuntimeError("Codex Platform MCP gateway failed to start")
                await asyncio.sleep(0.01)
            else:
                raise RuntimeError("Codex Platform MCP gateway startup timed out")
        except BaseException:
            server.should_exit = True
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            sock.close()
            await session_context.__aexit__(None, None, None)
            raise

        self._mcp = mcp
        self._session_manager_context = session_context
        self._socket = sock
        self._server = server
        self._server_task = task
        self.url = f"http://127.0.0.1:{port}/"

    async def close(self) -> None:
        self.deactivate()
        server = self._server
        task = self._server_task
        session_context = self._session_manager_context
        sock = self._socket
        self._server = None
        self._server_task = None
        self._session_manager_context = None
        self._socket = None
        self._mcp = None
        self.url = None
        if server is not None:
            server.should_exit = True
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except asyncio.TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        if sock is not None:
            sock.close()
        if session_context is not None:
            await session_context.__aexit__(None, None, None)


__all__ = ["CodexPlatformMcpGateway"]
