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
import json
import os
import socket
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import uvicorn
import websockets
from mcp import ClientSession, types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.fastmcp import FastMCP

from vibecanvas_api.services.agent_runtime.approval import (
    is_pre_tool_approval_candidate,
)
from vibecanvas_api.services.agent_runtime.protocol import RuntimeMcpServer
from vibecanvas_api.browser.playwright_contract import (
    filter_playwright_tools,
    playwright_tool_is_allowed,
)

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


def _tool_not_allowed_result(tool_name: str) -> types.CallToolResult:
    message = "This Playwright tool is outside the reviewed Skeinix browser surface."
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=message)],
        structuredContent={
            "status": "error",
            "error": {"code": "browser_tool_not_allowed", "message": message},
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
        self.descriptor = descriptor
        self._request_approval: ApprovalCallback | None = None
        self._requires_approval: ApprovalPredicate | None = None
        self._upstream_url = ""
        self._upstream_headers: dict[str, str] = {}
        self._upstream_connection: dict[str, Any] = {}
        self._upstream_revision = 0
        self._connected_upstream_revision = -1
        self._remote_session_instance: ClientSession | None = None
        self._remote_owner_task: asyncio.Task[None] | None = None
        self._remote_stop_event: asyncio.Event | None = None
        self._playwright_ws_relay: Any = None
        self._remote_lock = asyncio.Lock()
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
        transport = str(connection.get("transport") or "")
        if transport not in {"streamable_http", "stdio"}:
            raise ValueError(
                "Codex Platform MCP gateway requires Streamable HTTP or stdio"
            )
        self.descriptor = descriptor
        connection_key = json.dumps(connection, sort_keys=True, separators=(",", ":"))
        old_key = json.dumps(
            self._upstream_connection, sort_keys=True, separators=(",", ":")
        )
        if connection_key != old_key:
            self._upstream_revision += 1
        self._upstream_connection = connection
        self._upstream_url = str(connection.get("url") or "")
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

    async def disconnect_upstream(self) -> None:
        """Release the Turn-scoped upstream process/session.

        The loopback gateway remains resident for Codex, but no browser MCP
        child or private capability survives between Agent Turns.
        """
        async with self._remote_lock:
            await self._close_remote_session_locked()

    async def _close_remote_session_locked(self) -> None:
        owner_task = self._remote_owner_task
        stop_event = self._remote_stop_event
        relay = self._playwright_ws_relay
        self._remote_session_instance = None
        self._remote_owner_task = None
        self._remote_stop_event = None
        self._playwright_ws_relay = None
        self._connected_upstream_revision = -1
        if stop_event is not None:
            stop_event.set()
        if owner_task is not None:
            try:
                await asyncio.wait_for(asyncio.shield(owner_task), timeout=15.0)
            except asyncio.TimeoutError:
                owner_task.cancel()
                await asyncio.gather(owner_task, return_exceptions=True)
        if relay is not None:
            relay.close()
            await relay.wait_closed()

    @staticmethod
    async def _start_playwright_websocket_relay(
        *,
        upstream_url: str,
        expected_bearer: str,
    ) -> tuple[Any, str, dict[str, str]]:
        """Bridge official Playwright CDP through the sandbox egress proxy.

        Agent Runtime sandboxes intentionally have no network device. Their
        HTTP(S) traffic uses the loopback egress proxy, but Playwright's
        ``connectOverCDP`` installs its own WebSocket agent and therefore does
        not honor ``HTTP_PROXY``. Keep Playwright unmodified: it connects to
        this turn-local loopback WebSocket, while the trusted Runtime relay
        opens the authenticated upstream through the same policy-enforcing
        egress proxy as every other private Platform connection.

        The relay validates the exact short-lived Platform capability before
        forwarding and never listens outside 127.0.0.1.
        """
        if not upstream_url.startswith(("ws://", "wss://")):
            raise ValueError("Playwright CDP upstream must be an absolute ws(s) URL")
        if not expected_bearer or any(char in expected_bearer for char in "\r\n"):
            raise ValueError("Playwright CDP bearer is missing or invalid")

        proxy = os.environ.get("VC_RUNTIME_EGRESS_PROXY") or None

        relay_state: dict[str, str] = {}

        def safe_error(exc: BaseException) -> str:
            detail = str(exc).replace(expected_bearer, "[redacted]")
            return f"{type(exc).__name__}: {detail[:500]}"

        async def relay_connection(local: Any) -> None:
            authorization = str(
                local.request.headers.get("Authorization") or ""
            )
            if authorization != f"Bearer {expected_bearer}":
                await local.close(code=4401, reason="Unauthorized")
                return
            try:
                async with websockets.connect(
                    upstream_url,
                    additional_headers={
                        "Authorization": f"Bearer {expected_bearer}",
                    },
                    proxy=proxy,
                    # This is a private control-plane hop, not a page load. A
                    # dead relay must fail promptly so the Runtime can report
                    # and reconnect instead of leaving the user waiting for a
                    # full minute. Fifteen seconds still tolerates proxy/VPN
                    # handshakes without hiding an unhealthy route.
                    open_timeout=15,
                    ping_interval=20,
                    ping_timeout=20,
                    close_timeout=10,
                    max_size=None,
                    compression=None,
                ) as upstream:
                    async def pump(source: Any, destination: Any) -> None:
                        async for message in source:
                            await destination.send(message)

                    local_to_upstream = asyncio.create_task(
                        pump(local, upstream)
                    )
                    upstream_to_local = asyncio.create_task(
                        pump(upstream, local)
                    )
                    done, pending = await asyncio.wait(
                        {local_to_upstream, upstream_to_local},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(
                        *done,
                        *pending,
                        return_exceptions=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep the upstream handshake failure available to the
                # supervising Runtime.  Without this, Playwright only sees a
                # generic 1011 close and the sandbox host cannot distinguish a
                # rejected capability from a proxy/DNS failure.  The Turn
                # capability is always redacted before it can reach logs or a
                # client-facing Runtime error.
                relay_state["last_error"] = safe_error(exc)
                if local.state.name not in {"CLOSING", "CLOSED"}:
                    await local.close(code=1011, reason="Upstream unavailable")

        server = await websockets.serve(
            relay_connection,
            "127.0.0.1",
            0,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=10,
            max_size=None,
            compression=None,
        )
        sockets = list(server.sockets or [])
        if not sockets:
            server.close()
            await server.wait_closed()
            raise RuntimeError("Playwright WebSocket relay exposed no socket")
        port = int(sockets[0].getsockname()[1])
        return server, f"ws://127.0.0.1:{port}/", relay_state

    async def _remote_http_session(
        self, stack: contextlib.AsyncExitStack
    ) -> ClientSession:
        client = await stack.enter_async_context(
            httpx.AsyncClient(
                headers=self._upstream_headers,
                follow_redirects=True,
                timeout=httpx.Timeout(60.0, read=None),
                # Never let an ambient developer/host proxy intercept a
                # short-lived Platform capability header.
                trust_env=False,
                proxy=os.environ.get("VC_RUNTIME_EGRESS_PROXY") or None,
            )
        )
        streams = await stack.enter_async_context(
            streamable_http_client(self._upstream_url, http_client=client)
        )
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        await session.initialize()
        return session

    async def _ensure_remote_session(self) -> ClientSession:
        """Keep one upstream MCP session for the whole active Agent Turn.

        Playwright snapshot refs, tab state, dialogs and auto-wait state live in
        the upstream MCP process. Re-launching stdio for every tool call would
        make a snapshot handle unusable by the following click and recreate the
        exact instability the official MCP integration is meant to remove.
        """
        async with self._remote_lock:
            if (
                self._remote_session_instance is not None
                and self._remote_owner_task is not None
                and not self._remote_owner_task.done()
                and self._connected_upstream_revision == self._upstream_revision
            ):
                return self._remote_session_instance
            await self._close_remote_session_locked()
            playwright_relay = None
            playwright_relay_state: dict[str, str] | None = None
            owner_task: asyncio.Task[None] | None = None
            try:
                connection = dict(self._upstream_connection)
                transport = str(connection.get("transport") or "")
                if transport == "stdio":
                    child_env = dict(os.environ)
                    child_env.update(
                        {
                            str(key): str(value)
                            for key, value in dict(connection.get("env") or {}).items()
                        }
                    )
                    playwright_endpoint = str(
                        child_env.get("SKEINIX_PLAYWRIGHT_CDP_ENDPOINT") or ""
                    )
                    playwright_bearer = str(
                        child_env.get("SKEINIX_PLAYWRIGHT_CDP_BEARER") or ""
                    )
                    if playwright_endpoint or playwright_bearer:
                        # The gVisor rootfs intentionally has no writable
                        # ``/root``.  This trusted gateway process runs as uid 0,
                        # so Node's os.homedir() would otherwise lead the
                        # official Playwright MCP to create its profile cache at
                        # ``/root`` on the first real browser tool call.  Keep
                        # all disposable MCP profile/cache state on the
                        # sandbox-local tmpfs instead; no host path or
                        # additional permission is exposed.
                        playwright_home = "/tmp/skeinix-playwright-mcp"
                        playwright_cache = f"{playwright_home}/cache"
                        os.makedirs(playwright_cache, mode=0o700, exist_ok=True)
                        child_env.update(
                            {
                                "HOME": playwright_home,
                                "XDG_CACHE_HOME": playwright_cache,
                                "TMPDIR": "/tmp",
                            }
                        )
                        (
                            playwright_relay,
                            local_endpoint,
                            playwright_relay_state,
                        ) = (
                            await self._start_playwright_websocket_relay(
                                upstream_url=playwright_endpoint,
                                expected_bearer=playwright_bearer,
                            )
                        )
                        child_env["SKEINIX_PLAYWRIGHT_CDP_ENDPOINT"] = (
                            local_endpoint
                        )
                else:
                    raise RuntimeError(
                        "Persistent upstream sessions are reserved for stdio MCPs"
                    )

                # MCP's stdio transport owns an AnyIO TaskGroup/cancel scope.
                # Its async context must be exited by the same task that
                # entered it, and that cancel scope must remain the task's
                # outermost active scope. Holding an AsyncExitStack open in the
                # Codex Turn task violated that invariant after normal model or
                # browser awaits, causing cleanup to abort with "Attempted to
                # exit a cancel scope that isn't current". A dedicated owner
                # task keeps the transport lifecycle well-nested while the
                # initialized ClientSession remains safely callable by gateway
                # request handlers.
                ready: asyncio.Future[ClientSession] = (
                    asyncio.get_running_loop().create_future()
                )
                stop_event = asyncio.Event()

                async def own_stdio_session() -> None:
                    try:
                        async with contextlib.AsyncExitStack() as stack:
                            streams = await stack.enter_async_context(
                                stdio_client(
                                    StdioServerParameters(
                                        command=str(connection["command"]),
                                        args=[
                                            str(arg)
                                            for arg in connection.get("args") or []
                                        ],
                                        env=child_env,
                                        cwd=(
                                            str(connection["cwd"])
                                            if connection.get("cwd")
                                            else None
                                        ),
                                    )
                                )
                            )
                            session = await stack.enter_async_context(
                                ClientSession(streams[0], streams[1])
                            )
                            await session.initialize()
                            if not ready.done():
                                ready.set_result(session)
                            await stop_event.wait()
                    except BaseException as exc:
                        if not ready.done():
                            ready.set_exception(exc)
                            return
                        raise

                owner_task = asyncio.create_task(own_stdio_session())
                session = await ready
            except BaseException as exc:
                if owner_task is not None:
                    owner_task.cancel()
                    await asyncio.gather(owner_task, return_exceptions=True)
                if playwright_relay is not None:
                    playwright_relay.close()
                    await playwright_relay.wait_closed()
                relay_error = (
                    playwright_relay_state.get("last_error")
                    if playwright_relay_state is not None
                    else None
                )
                if relay_error:
                    raise RuntimeError(
                        "Playwright CDP relay could not open its authenticated "
                        f"upstream: {relay_error}"
                    ) from exc
                raise
            self._remote_session_instance = session
            self._remote_owner_task = owner_task
            self._remote_stop_event = stop_event
            self._playwright_ws_relay = playwright_relay
            self._connected_upstream_revision = self._upstream_revision
            return session

    async def _list_upstream_tools(self) -> list[types.Tool]:
        if self._upstream_connection.get("transport") == "stdio":
            session = await self._ensure_remote_session()
            result = await session.list_tools()
            tools = list(result.tools)
            return (
                filter_playwright_tools(tools)
                if self.descriptor.name == "browser"
                else tools
            )
        async with contextlib.AsyncExitStack() as stack:
            session = await self._remote_http_session(stack)
            result = await session.list_tools()
            tools = list(result.tools)
            return (
                filter_playwright_tools(tools)
                if self.descriptor.name == "browser"
                else tools
            )

    async def _forward_call(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        if self._upstream_connection.get("transport") == "stdio":
            session = await self._ensure_remote_session()
            return await session.call_tool(name, arguments)
        async with contextlib.AsyncExitStack() as stack:
            session = await self._remote_http_session(stack)
            return await session.call_tool(name, arguments)

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> types.CallToolResult:
        """Apply product policy, then forward the original CallTool at most once."""
        if not self._active:
            return _inactive_result(name)
        if self.descriptor.name == "browser" and not playwright_tool_is_allowed(name):
            return _tool_not_allowed_result(name)
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
            # activate() may have installed a fresh Turn-scoped capability.
            # Reconnect the private upstream before Codex receives this stable
            # loopback gateway URL.
            self._tools = await self._list_upstream_tools()
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
        await self.disconnect_upstream()


__all__ = ["CodexPlatformMcpGateway"]
