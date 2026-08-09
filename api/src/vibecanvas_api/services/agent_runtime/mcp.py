"""Standard MCP loading at the Agent Runtime boundary.

Remote custom MCP servers are exposed through a host-side, capability-bound
broker.  Their bearer/OAuth/header/query credentials therefore never cross
the host/sandbox protocol.  Secretless stdio registrations still execute in
the Chat sandbox with the official LangChain MCP adapter.
"""

from __future__ import annotations

import asyncio
import copy
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any, Iterable

from vibecanvas_api.agents.tools import builtin_tool_names
from vibecanvas_api.config import config
from vibecanvas_api.services.agent_runtime.custom_mcp_capability import (
    mcp_config_revision,
    mint_runtime_custom_mcp_capability,
)
from vibecanvas_api.services.agent_runtime.model_capability import (
    authorization_model_generation,
)
from vibecanvas_api.services.agent_runtime.protocol import RuntimeMcpServer
from vibecanvas_api.services.mcp_config import (
    server_descriptor,
    validate_mcp_connection_destination,
)
from vibecanvas_api.services.platform_mcp.capability import (
    mint_platform_mcp_capability,
)
from vibecanvas_api.services.platform_mcp.catalog import platform_mcp_description
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_mcp_servers import McpServersRepo

log = logging.getLogger(__name__)

_RUNTIME_MCP_CACHE_TTL_S = 300.0
_RUNTIME_MCP_TOOL_CACHE: dict[
    tuple[str, str, str, str, tuple[str, ...]],
    tuple[float, tuple[Any, ...]],
] = {}
_MCP_HANDSHAKE_RETRIES = 1

_PLATFORM_PATHS = {
    "config": "/api/internal/mcp/config/",
    "interactive": "/api/internal/mcp/interactive/",
    "workflow": "/api/internal/mcp/workflow/",
    "task": "/api/internal/mcp/task/",
    "deployment": "/api/internal/mcp/deployment/",
    "knowledge": "/api/internal/mcp/knowledge/",
    "build": "/api/internal/mcp/build/",
    "browser": "/api/internal/mcp/browser/",
    "plan": "/api/internal/mcp/plan/",
    "diagram": "/api/internal/mcp/diagram/",
}

BASE_PLATFORM_MCPS = (
    "config",
    "interactive",
    "workflow",
)


def platform_mcp_names_for_modes(
    active_modes: Iterable[str],
    *,
    runtime_type: str | None = None,
) -> list[str]:
    """Return the stable base capabilities plus command-activated capabilities.

    This is the single command-to-MCP routing table used by ordinary and
    background Turns. Keeping the mapping outside either Runtime adapter means
    LangChain and Codex receive the same platform capabilities for a Chat.
    """
    modes = set(active_modes)
    command_servers = [
        name
        for name in (
            "task",
            "deployment",
            "knowledge",
            "build",
            "browser",
            "plan",
            "diagram",
        )
        if name in modes
    ]
    # Background execution is a LangChain subagent implementation detail, not
    # a platform MCP. Every MCP listed here is usable by both Runtime families.
    if "plan" in modes and runtime_type != "langchain":
        raise ValueError("/plan requires the LangChain Runtime")
    return [*BASE_PLATFORM_MCPS, *command_servers]


class McpSelectionError(ValueError):
    """A selected MCP cannot be resolved safely for this Turn."""


async def custom_mcp_descriptors(
    tenant_id: str,
    *,
    user_id: str,
    chat_id: str,
    turn_id: str,
    runtime_session_id: str,
    session_id: str,
    session_generation: int,
    membership_id: str,
    server_ids: Iterable[str],
) -> list[RuntimeMcpServer]:
    """Resolve selected MCP rows without exposing remote credentials.

    Remote descriptors contain only an internal broker URL and a short-lived
    capability.  The broker resolves OAuth/bearer/header/query material only
    after revalidating the live browser Session, membership, Agent Run, Chat,
    and MCP installation.  Stdio has no safe host proxy equivalent: only
    registrations with no stored auth/env/query secret may run directly in the
    Chat sandbox.
    """

    selected = {str(server_id) for server_id in server_ids}
    if not selected:
        return []
    async with session_scope(tenant_id=str(tenant_id)) as session:
        rows = await McpServersRepo(session).list_enabled_for_user(user_id)
        selected_rows = [row for row in rows if str(row["id"]) in selected]
    found = {str(row["id"]) for row in selected_rows}
    missing = sorted(selected - found)
    if missing:
        raise McpSelectionError(
            "Selected MCP servers are unavailable or not owned by this user: "
            + ", ".join(missing)
        )

    descriptors: list[RuntimeMcpServer] = []
    for row in sorted(selected_rows, key=lambda item: str(item["tool_prefix"])):
        # Never resolve auth_config or connection_secret_ref here.  The stored
        # projection is sufficient to validate the structural destination and
        # decide whether the server is remote or stdio.
        serialized = server_descriptor({**row, "auth_config": {"type": "none"}})
        connection = serialized["connection"]
        try:
            await validate_mcp_connection_destination(connection)
        except ValueError as exc:
            raise McpSelectionError(
                f"MCP server {row['name']!r} has an unsafe endpoint: {exc}"
            ) from exc
        revision = mcp_config_revision(
            server_id=row["id"],
            updated_at=row.get("updated_at"),
        )
        transport = str(connection.get("transport") or "")
        if transport == "stdio":
            inline_env = connection.get("env")
            has_auth = (
                bool(row.get("auth_secret_ref"))
                or bool(row.get("connection_secret_ref"))
                or str(row.get("auth_mode") or "none") != "none"
                or str((row.get("auth_config") or {}).get("type") or "none")
                != "none"
                or bool(inline_env)
            )
            if has_auth:
                raise McpSelectionError(
                    f"MCP server {row['name']!r} uses credentials that cannot "
                    "be exposed to the Chat sandbox"
                )
            runtime_connection = connection
        else:
            token = mint_runtime_custom_mcp_capability(
                organization_id=str(tenant_id),
                user_id=str(user_id),
                chat_id=str(chat_id),
                turn_id=str(turn_id),
                runtime_session_id=str(runtime_session_id),
                session_id=str(session_id),
                session_generation=int(session_generation),
                membership_id=str(membership_id),
                server_id=str(row["id"]),
                transport=transport,
                config_revision=revision,
                authorization_generation=authorization_model_generation(
                    model_id=config.openfga_authorization_model_id,
                ),
                secret=config.signing_secret,
                ttl_s=config.mcp.platform_capability_ttl_s,
            )
            runtime_connection = {
                "transport": transport,
                "url": (
                    f"{config.mcp.platform_internal_base_url}"
                    f"/api/internal/runtime-mcp/v1/{row['id']}"
                ),
                "headers": {"Authorization": f"Bearer {token}"},
            }
            # Adapter behavior controls are not credentials and can safely be
            # retained. The real endpoint, headers and query stay host-side.
            for key in ("timeout", "sse_read_timeout", "terminate_on_close"):
                if key in connection:
                    runtime_connection[key] = connection[key]
        descriptors.append(
            RuntimeMcpServer(
                name=str(row["tool_prefix"]),
                source="custom",
                description=str(row.get("description") or ""),
                connection=runtime_connection,
                server_id=str(row["id"]),
                config_revision=revision,
                required=False,
            )
        )
    return descriptors


def platform_mcp_descriptors(
    servers: Iterable[str],
    *,
    tenant_id: str,
    user_id: str,
    chat_id: str,
    turn_id: str,
    workspace_scope_id: str,
    runtime_session_id: str,
    session_id: str,
    session_generation: int,
    membership_id: str,
    approval_mode: str = "agent",
) -> list[RuntimeMcpServer]:
    """Mint least-privilege descriptors for explicitly activated capabilities."""
    result: list[RuntimeMcpServer] = []
    for server in servers:
        path = _PLATFORM_PATHS.get(server)
        if path is None:
            raise ValueError(f"unknown platform MCP capability: {server}")
        token = mint_platform_mcp_capability(
            organization_id=tenant_id,
            user_id=user_id,
            chat_id=chat_id,
            turn_id=turn_id,
            workspace_scope_id=workspace_scope_id,
            runtime_session_id=runtime_session_id,
            session_id=session_id,
            session_generation=session_generation,
            membership_id=membership_id,
            server=server,
            authorization_generation=authorization_model_generation(
                model_id=config.openfga_authorization_model_id,
            ),
            approval_mode=approval_mode,
            secret=config.signing_secret,
            ttl_s=config.mcp.platform_capability_ttl_s,
        )
        result.append(
            RuntimeMcpServer(
                name=server,
                source="platform",
                description=platform_mcp_description(server),
                connection={
                    "transport": "streamable_http",
                    "url": f"{config.mcp.platform_internal_base_url}{path}",
                    "headers": {"Authorization": f"Bearer {token}"},
                },
                required=True,
            )
        )
    return result


def _catalog_entry(
    server: RuntimeMcpServer,
    *,
    loaded: bool,
    tool_count: int | None = None,
    error: str | None = None,
    cache_status: str = "not_cacheable",
    handshake_ms: int = 0,
    retry_count: int = 0,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": server.name,
        "server_id": server.server_id,
        "description": server.description,
        "loaded": loaded,
        "source": server.source,
        "tool_count": tool_count,
        "health": "ready" if loaded else "degraded",
        "cache_status": cache_status,
        "handshake_ms": max(0, handshake_ms),
        "retry_count": max(0, retry_count),
        "config_revision": server.config_revision,
    }
    if tools is not None:
        result["tools"] = tools
    if error:
        result["error"] = error
    return result


def _runtime_cache_key(
    server: RuntimeMcpServer,
) -> tuple[str, str, str, str, tuple[str, ...]] | None:
    """Return a secret-free cache key for reusable sandbox-local stdio tools."""
    connection = server.connection
    if server.source != "custom" or connection.get("transport") != "stdio":
        return None
    # Host selection rejects stdio env/auth before it reaches the Runtime. Keep
    # the invariant here too so a future caller cannot cache secret material.
    if connection.get("env"):
        return None
    return (
        server.server_id or server.name,
        server.config_revision or "unversioned",
        str(connection.get("command") or ""),
        str(connection.get("cwd") or ""),
        tuple(str(item) for item in connection.get("args") or []),
    )


def _clear_runtime_mcp_tool_cache() -> None:
    """Test/admin hook; ordinary invalidation is revision + TTL based."""
    _RUNTIME_MCP_TOOL_CACHE.clear()


@dataclass(frozen=True, slots=True)
class _McpLoadResult:
    tools: list[Any]
    cache_status: str
    handshake_ms: int
    retry_count: int


def _tool_manifest(tool: Any, *, server: RuntimeMcpServer) -> dict[str, Any]:
    """Return stable, secret-free registry metadata for one loaded tool."""
    raw_name = str(getattr(tool, "name", "") or "")
    args_schema = getattr(tool, "args_schema", None)
    try:
        input_schema = args_schema.model_json_schema() if args_schema is not None else None
    except Exception:
        input_schema = None
    return {
        "id": f"{server.source}:{server.name}:{raw_name}",
        "name": raw_name,
        "description": str(getattr(tool, "description", "") or ""),
        "origin": "platform_mcp" if server.source == "platform" else "custom_mcp",
        "capability": server.name,
        "risk": "unknown",
        "load_policy": "command_only" if server.source == "platform" else "always",
        "required_policy": "required" if server.required else "optional",
        "runtime_compatibility": ["langchain", "codex"],
        "input_schema": input_schema,
        "version": server.config_revision or "unversioned",
    }


async def _load_server_tools(server: RuntimeMcpServer) -> _McpLoadResult:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    cache_key = _runtime_cache_key(server)
    if cache_key is not None:
        cached = _RUNTIME_MCP_TOOL_CACHE.get(cache_key)
        if cached is not None and monotonic() - cached[0] < _RUNTIME_MCP_CACHE_TTL_S:
            return _McpLoadResult(
                tools=[copy.copy(tool) for tool in cached[1]],
                cache_status="hit",
                handshake_ms=0,
                retry_count=0,
            )
        if cached is not None:
            _RUNTIME_MCP_TOOL_CACHE.pop(cache_key, None)

    started = monotonic()
    retries = 0
    while True:
        client = MultiServerMCPClient(
            {server.name: dict(server.connection)},
            handle_tool_errors=True,
        )
        try:
            loaded = list(await asyncio.wait_for(
                client.get_tools(server_name=server.name),
                timeout=float(config.mcp.handshake_timeout_s),
            ))
            break
        except (TimeoutError, OSError, ConnectionError):
            if retries >= _MCP_HANDSHAKE_RETRIES:
                raise
            retries += 1
            # One immediate retry covers transient connection establishment
            # without adding an unbounded delay before first token.
            await asyncio.sleep(0)
    if cache_key is not None:
        _RUNTIME_MCP_TOOL_CACHE[cache_key] = (
            monotonic(),
            tuple(copy.copy(tool) for tool in loaded),
        )
    return _McpLoadResult(
        tools=loaded,
        cache_status="miss" if cache_key is not None else "fresh",
        handshake_ms=int((monotonic() - started) * 1000),
        retry_count=retries,
    )


async def load_runtime_mcp_tools(
    servers: Iterable[RuntimeMcpServer],
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Load MCP tools through ``MultiServerMCPClient`` inside the sandbox.

    Custom server connection failures are isolated to that server. Platform
    servers are command-scoped privileged capabilities, so failure is closed:
    a ``/build`` or ``/browser`` turn must never silently run without the
    capability the user explicitly activated.
    """

    server_list = sorted(
        list(servers),
        key=lambda item: (0 if item.source == "platform" else 1, item.name),
    )
    if not server_list:
        return [], []

    reserved = builtin_tool_names()
    seen = set(reserved)
    tools: list[Any] = []
    catalog: list[dict[str, Any]] = []
    tenant_cap = int(config.mcp.per_tenant_tool_cap)
    server_cap = int(config.mcp.per_server_tool_cap)

    # Handshakes/list-tools are independent and latency-bound. Normalize and
    # apply global limits below in deterministic server order.
    loaded_results = await asyncio.gather(
        *(_load_server_tools(server) for server in server_list),
        return_exceptions=True,
    )

    for server, loaded_result in zip(server_list, loaded_results, strict=True):
        try:
            if isinstance(loaded_result, BaseException):
                raise loaded_result
            load = loaded_result
            loaded = load.tools
            if len(loaded) > server_cap:
                raise RuntimeError(
                    f"server exported {len(loaded)} tools; limit is {server_cap}"
                )
            if len(tools) + len(loaded) > tenant_cap:
                raise RuntimeError(
                    f"tenant MCP tool limit {tenant_cap} would be exceeded"
                )

            normalized = []
            for tool in loaded:
                raw_name = str(getattr(tool, "name", "") or "")
                if not raw_name:
                    raise RuntimeError("server exported a tool without a name")
                # Platform tools retain the established model-facing names
                # documented by /build and /browser. Custom servers remain
                # namespaced to avoid collisions with platform/base tools.
                qualified = (
                    raw_name
                    if server.source == "platform"
                    else f"{server.name}__{raw_name}"
                )
                if server.source == "platform":
                    reserved.discard(qualified)
                    seen.discard(qualified)
                if qualified in seen:
                    raise RuntimeError(f"duplicate MCP tool name: {qualified}")
                # The official adapter's coroutine retains the original MCP
                # CallTool name; only the model-facing LangChain name changes.
                tool.name = qualified
                seen.add(qualified)
                normalized.append(tool)
            tools.extend(normalized)
            catalog.append(
                _catalog_entry(
                    server,
                    loaded=True,
                    tool_count=len(normalized),
                    cache_status=load.cache_status,
                    handshake_ms=load.handshake_ms,
                    retry_count=load.retry_count,
                    tools=[_tool_manifest(tool, server=server) for tool in normalized],
                )
            )
        except Exception as exc:
            if server.required:
                label = "platform MCP" if server.source == "platform" else "required MCP"
                raise RuntimeError(
                    f"{label} {server.name} could not be loaded: {exc}"
                ) from exc
            log.warning(
                "custom MCP %s could not be loaded in runtime: %s",
                server.name,
                exc,
            )
            catalog.append(
                _catalog_entry(server, loaded=False, error=str(exc))
            )

    return tools, catalog
