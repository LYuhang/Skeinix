"""Official Streamable HTTP MCP facades for privileged platform tools."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from langchain.tools import ToolRuntime
from mcp import types
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from vibecanvas_api.agent import AgentContext
from vibecanvas_api.auth.deps import AuthContext
from vibecanvas_api.auth.live_identity import (
    LiveIdentityError,
    resolve_live_authorization_identity,
)
from vibecanvas_api.authorization.dependencies import (
    authz_service_for_session,
    scope_authz_service,
)
from vibecanvas_api.authorization.openfga_client import OpenFgaUnavailableError
from vibecanvas_api.authorization.types import (
    Action,
    AuthzRequestContext,
    ConsistencyPreference,
    PrincipalRef,
    PrincipalType,
    ResourceRef,
    ResourceType,
)
from vibecanvas_api.config import config
from vibecanvas_api.diagrams.mcp_contract import (
    diagram_input_schema,
    diagram_output_schema,
)
from vibecanvas_api.services.chat_workspace import chat_workspace_scope_id
from vibecanvas_api.services.platform_mcp.authorization import (
    platform_mcp_tool_action,
    platform_resource_tool_action,
    prepare_platform_tool,
)
from vibecanvas_api.services.platform_mcp.build_tools import BUILD_TOOLS
from vibecanvas_api.services.platform_mcp.build_tools.workflow_context import (
    create_workflow,
    set_workflow,
)
from vibecanvas_api.services.platform_mcp.capability import (
    PlatformMcpCapability,
    verify_platform_mcp_capability,
)
from vibecanvas_api.services.platform_mcp.catalog import PLATFORM_MCP_METADATA
from vibecanvas_api.services.platform_mcp.config_tools import CONFIG_TOOLS
from vibecanvas_api.services.platform_mcp.diagram_tools import DIAGRAM_TOOLS
from vibecanvas_api.services.platform_mcp.interactive_tools import INTERACTIVE_TOOLS
from vibecanvas_api.services.platform_mcp.plan_tools import PLAN_TOOLS
from vibecanvas_api.services.platform_mcp.resource_tools import (
    DEPLOYMENT_MCP_TOOLS,
    KNOWLEDGE_MCP_TOOLS,
    TASK_MCP_TOOLS,
)
from vibecanvas_api.services.platform_mcp.run_tools import RUN_TOOLS
from vibecanvas_api.services.platform_mcp.workflow_tools import (
    WORKFLOW_MCP_TOOLS,
)
from vibecanvas_api.storage.agent_runs_repo import AgentRunsRepo
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.sync_repo import SyncWorkflowRepo
from vibecanvas_api.storage.sync_session import current_sync_tenant_id
from vibecanvas_api.storage.vfs_store import PostgresVfsStore

PLATFORM_MCP_PATHS = {
    "config": "/api/internal/mcp/config",
    "interactive": "/api/internal/mcp/interactive",
    "workflow": "/api/internal/mcp/workflow",
    "task": "/api/internal/mcp/task",
    "deployment": "/api/internal/mcp/deployment",
    "knowledge": "/api/internal/mcp/knowledge",
    "build": "/api/internal/mcp/build",
    "plan": "/api/internal/mcp/plan",
    "diagram": "/api/internal/mcp/diagram",
}

_OPENFGA_CLIENT = None


def set_platform_mcp_openfga_client(client) -> None:
    """Publish the API lifespan's shared authorization client to MCP apps."""
    global _OPENFGA_CLIENT
    _OPENFGA_CLIENT = client


class _CapabilityVerifier(TokenVerifier):
    def __init__(self, server: str) -> None:
        self.server = server

    async def verify_token(self, token: str) -> AccessToken | None:
        capability = verify_platform_mcp_capability(
            token,
            secret=config.signing_secret,
            server=self.server,
        )
        if capability is None:
            return None
        return AccessToken(
            token=token,
            client_id=capability.user_id,
            scopes=[f"platform:{self.server}"],
            expires_at=capability.exp,
            resource=platform_mcp_url(self.server),
        )


def platform_mcp_url(server: str) -> str:
    try:
        path = PLATFORM_MCP_PATHS[server]
    except KeyError as exc:
        raise ValueError(f"unknown platform MCP server: {server}") from exc
    # The mounted FastMCP app serves its transport at '/'.
    return f"{config.mcp.platform_internal_base_url}{path}/"


def _platform_transport_security(origin: str) -> TransportSecuritySettings:
    """Allow only the configured private Platform MCP service authority.

    FastMCP assumes a loopback-only deployment when its ``host`` argument is
    left at the default, so its DNS-rebinding middleware otherwise rejects the
    Docker service name (for example ``api:8000``) with HTTP 421.  Platform MCP
    is mounted inside the API rather than started with ``FastMCP.run()``, which
    makes the configured private origin the authoritative allowlist source.
    """
    parts = urlsplit(origin)
    hostname = parts.hostname
    if not hostname:
        raise ValueError("Platform MCP internal origin must include a hostname")
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    authority = (
        f"{rendered_host}:{parts.port}"
        if parts.port is not None
        else rendered_host
    )
    allowed_origin = f"{parts.scheme}://{authority}"
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[authority],
        allowed_origins=[allowed_origin],
    )


def _capability(server: str) -> PlatformMcpCapability:
    access_token = get_access_token()
    if access_token is None:
        raise PermissionError("missing Platform MCP capability")
    capability = verify_platform_mcp_capability(
        access_token.token,
        secret=config.signing_secret,
        server=server,
    )
    if capability is None:
        raise PermissionError("invalid or expired Platform MCP capability")
    return capability


async def _context_for(capability: PlatformMcpCapability) -> AgentContext:
    """Rebuild ephemeral tool context from durable backend state."""
    identity = await _live_platform_identity(capability)
    async with session_scope(tenant_id=capability.tenant_id) as session:
        service = authz_service_for_session(
            session=session,
            organization_id=capability.organization_id,
            openfga_client=_OPENFGA_CLIENT,
        )
        service = scope_authz_service(
            service,
            session=session,
            auth=identity,
            audit_uses=True,
        )
        try:
            chat_decision = await service.check(
                PrincipalRef(PrincipalType.USER, capability.user_id),
                Action.EXECUTE,
                ResourceRef(
                    ResourceType.CHAT,
                    capability.chat_id,
                    capability.organization_id,
                ),
                AuthzRequestContext(
                    active_organization_id=capability.organization_id,
                    request_id=f"platform-mcp:{capability.turn_id}",
                    session_id=capability.session_id,
                    session_generation=capability.session_generation,
                    membership_id=identity.membership_id,
                    membership_role=identity.membership_role,
                    membership_status=identity.membership_status,
                    authentication_strength=identity.authentication_strength,
                    consistency=ConsistencyPreference.HIGHER_CONSISTENCY,
                ),
            )
        except OpenFgaUnavailableError as exc:
            raise PermissionError(
                "Platform MCP authorization is temporarily unavailable"
            ) from exc
        if not chat_decision.allowed:
            raise PermissionError("Platform MCP Chat access has been revoked")
        run = await AgentRunsRepo(session).get_for_chat(
            capability.chat_id,
            capability.turn_id,
            creator_user_id=capability.user_id,
        )
        if run is None or run.status != "running":
            raise PermissionError("Platform MCP capability is not bound to an active run")
        binding = await ChatRepo(
            session, capability.user_id
        ).get_platform_context_binding(capability.chat_id)
        if binding is None:
            raise PermissionError("Platform MCP capability is not bound to an active Chat")
        if binding.get("runtime_session_id") != capability.runtime_session_id:
            raise PermissionError(
                "Platform MCP capability Runtime binding is stale"
            )
        if capability.server == "plan" and binding.get("runtime_type") != "langchain":
            raise PermissionError("Plan MCP requires an active LangChain Runtime binding")
        expected_workspace_scope_id = chat_workspace_scope_id(
            capability.chat_id
        )
        if expected_workspace_scope_id != capability.workspace_scope_id:
            raise PermissionError(
                "Platform MCP capability workspace does not match its Chat"
            )
        current_workflow_id = binding["current_workflow_id"]

    workflow_repo = SyncWorkflowRepo(capability.user_id)

    return AgentContext(
        # Never preload selected workflow content before the concrete tool
        # action is authorized. _invoke populates it after the action check.
        workflow={},
        repo=workflow_repo,
        vfs=PostgresVfsStore(),
        username=capability.user_id,
        wf_id=capability.workspace_scope_id,
        tenant_id=capability.tenant_id,
        chat_id=capability.chat_id,
        turn_id=capability.turn_id,
        authorization_client=_OPENFGA_CLIENT,
        authorization_session_id=capability.session_id,
        authorization_membership_id=identity.membership_id,
        authorization_membership_role=identity.membership_role,
        authorization_membership_status=identity.membership_status,
        authorization_session_generation=capability.session_generation,
        authorization_generation=capability.authorization_generation,
        authorization_authentication_strength=identity.authentication_strength,
        authorization_session_audience=identity.session_audience,
        authorization_privileged_access_request_id=(
            identity.privileged_access_request_id
        ),
        authorization_privileged_resource_type=identity.privileged_resource_type,
        authorization_privileged_resource_id=identity.privileged_resource_id,
        authorization_privileged_actions=tuple(identity.privileged_actions),
        authorization_privileged_expires_at=identity.privileged_expires_at,
        runtime_session_id=capability.runtime_session_id,
        surface="chat",
        approval_mode=capability.approval_mode,
        runtime_location="platform_mcp",
        current_workflow_id=current_workflow_id,
    )


async def _live_platform_identity(
    capability: PlatformMcpCapability,
) -> AuthContext:
    """Fence a capability to the still-live browser Session and membership."""
    from vibecanvas_api.services.agent_runtime.model_capability import (
        authorization_model_generation,
    )

    expected_generation = authorization_model_generation(
        model_id=config.openfga_authorization_model_id,
    )
    if capability.authorization_generation != expected_generation:
        raise PermissionError("Platform MCP authorization generation is stale")

    async with session_scope() as identity_session:
        try:
            return await resolve_live_authorization_identity(
                identity_session,
                session_id=capability.session_id,
                user_id=capability.user_id,
                organization_id=capability.organization_id,
                session_generation=capability.session_generation,
                membership_id=capability.membership_id,
            )
        except LiveIdentityError as exc:
            raise PermissionError(
                "Platform MCP browser identity has been revoked"
            ) from exc


def _input_schema(tool: Any) -> dict[str, Any]:
    diagram_schema = diagram_input_schema(str(getattr(tool, "name", "")))
    if diagram_schema is not None:
        return diagram_schema
    properties = dict(getattr(tool, "args", {}) or {})
    required = [
        name
        for name, schema in properties.items()
        if isinstance(schema, dict) and "default" not in schema
    ]
    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def _tool_error_result(
    *,
    code: str,
    message: str,
    tool_name: str,
    json_pointer: str | None = None,
) -> types.CallToolResult:
    """Return an MCP-native, model-correctable tool error.

    Business/tool input errors are results with ``isError=true`` per the MCP
    specification.  They are not transport failures and never manufacture a
    downstream Diagram reference.
    """
    error = {
        "status": "error",
        "error": {
            "code": code,
            "message": message,
            **({"json_pointer": json_pointer} if json_pointer else {}),
        },
        "retry": {
            "tool": tool_name,
            "instruction": (
                "Correct only the rejected argument using this tool's "
                "published inputSchema and exact previously returned values."
            ),
        },
    }
    return types.CallToolResult(
        content=[types.TextContent(
            type="text",
            text=json.dumps(error, ensure_ascii=False),
        )],
        isError=True,
    )


def _output_schema(tool: Any) -> dict[str, Any] | None:
    """Publish a bounded structured result contract for Diagram lifecycle tools.

    The exact per-status payload is versioned inside each tool result. Other
    existing tools keep their established envelope-only contract.
    """
    return diagram_output_schema(str(getattr(tool, "name", "")))


def _annotations(name: str) -> types.ToolAnnotations:
    read_prefixes = (
        "list_",
        "get_",
        "check_",
    )
    read_only = name.startswith(read_prefixes) or name in {
        "task_list",
        "task_get",
        "deployment_list",
        "deployment_get",
        "list_knowledge_bases",
        "get_knowledge_base",
        "list_knowledge_files",
        "search_knowledge",
        "read_knowledge_file",
        "search_diagram_assets",
        "inspect_diagram",
        "read_diagram_review_image",
    }
    # Hints improve third-party client UX; security decisions still use the
    # backend-owned approval policy and concrete CallTool arguments.
    destructive = name in {
        "task_create_scheduled_run",
        "task_update_scheduled_run",
        "task_delete_scheduled_run",
        "task_cancel",
        "task_resume",
        "deployment_create",
        "deployment_update",
        "deployment_delete",
    }
    mutates_files = name in {
        "check_diagram",
        "present_diagram",
        "review_diagram",
        "export_diagram",
    }
    return types.ToolAnnotations(
        readOnlyHint=read_only and not mutates_files,
        destructiveHint=destructive,
        idempotentHint=read_only or mutates_files,
        openWorldHint=False,
    )


def _required_tool_actions(server: str, tool_name: str) -> frozenset[str]:
    """Map a registered tool to the signed action ceiling it consumes."""
    required = {"chat:execute", "platform_mcp:call"}
    if server in {"workflow", "build"}:
        action = platform_mcp_tool_action(tool_name)
        if tool_name == "create_workflow":
            required.add("organization:create")
        else:
            required.add(f"workflow:{action.value}")
        if server == "build" and tool_name in {
            "check_workflow",
            "update_canvas",
            "run_workflow",
            "node_execute",
            "batch_execute",
        }:
            required.add("vfs_path:view")
        if server == "build" and tool_name in {
            "update_canvas",
            "run_workflow",
            "node_execute",
            "batch_execute",
        }:
            required.add("vfs_path:update")
    elif server in {"task", "deployment", "knowledge"}:
        action = platform_resource_tool_action(server, tool_name)
        resource_type = {
            "task": "task",
            "deployment": "deployment",
            "knowledge": "knowledge_base",
        }[server]
        required.add(f"{resource_type}:{action.value}")
        if tool_name == "task_create_scheduled_run":
            required.update({"organization:create", "workflow:use"})
        elif tool_name == "deployment_create":
            required.update({"organization:create", "workflow:deploy"})
    elif server == "config" and tool_name == "get_config":
        required.update(
            {"platform_catalog:view", "llm_credential:view_metadata"}
        )
    elif server == "interactive" and tool_name == "render_interactive":
        required.update({
            "interactive_artifact:create",
            "vfs_path:view",
        })
    elif server == "plan" and tool_name == "create_execution_plan":
        required.update({"execution_plan:create", "vfs_path:view"})
    elif server == "diagram":
        if tool_name in {"get_diagram_spec", "search_diagram_assets"}:
            required.add("platform_catalog:view")
        elif tool_name in {
            "inspect_diagram",
            "review_diagram",
            "read_diagram_review_image",
        }:
            required.add("vfs_path:view")
        elif tool_name in {"check_diagram", "present_diagram", "export_diagram"}:
            required.update({"vfs_path:view", "vfs_path:update"})
        else:
            raise PermissionError(
                f"Platform MCP diagram tool {tool_name} has no capability policy"
            )
    else:
        raise PermissionError(
            f"Platform MCP tool {server}/{tool_name} has no capability policy"
        )
    return frozenset(required)


def _require_tool_capability(
    capability: PlatformMcpCapability,
    *,
    server: str,
    tool_name: str,
) -> None:
    required = _required_tool_actions(server, tool_name)
    if not required.issubset(capability.actions):
        raise PermissionError(
            f"Platform MCP capability does not permit {server}/{tool_name}"
        )


def _platform_events(context: AgentContext, tool_name: str) -> list[dict[str, Any]]:
    pending = context.pending_vibe
    if pending:
        workflow = pending.get("new_workflow") or {}
        meta = workflow.get("__meta__") or {}
        return [
            {
                "type": "VIBE_ACTION",
                "payload": {
                    "updates": list(pending.get("updates") or []),
                    "apply_auto_layout": bool(pending.get("apply_auto_layout")),
                    "workflow_id": meta.get("workflow_id"),
                    "workflow_version": meta.get("workflow_version"),
                    "workflow_subversion": meta.get("workflow_subversion"),
                },
            },
            {"type": "META_SYNC", "payload": {"meta": meta}},
        ]
    if tool_name in {"new_version", "set_workflow", "create_workflow"}:
        return [
            {
                "type": "META_SYNC",
                "payload": {"meta": (context.workflow or {}).get("__meta__", {})},
            }
        ]
    return []


async def _invoke(tool: Any, arguments: dict[str, Any], server: str):
    capability = _capability(server)
    _require_tool_capability(
        capability,
        server=server,
        tool_name=str(tool.name),
    )
    tenant_token = current_sync_tenant_id.set(capability.tenant_id)
    try:
        context = await _context_for(capability)
        runtime = ToolRuntime(
            state={},
            context=context,
            config={"configurable": {"thread_id": capability.chat_id}},
            stream_writer=lambda _chunk: None,
            tool_call_id=None,
            store=None,
            tools=[],
        )
        await prepare_platform_tool(
            context,
            server=server,
            tool_name=str(tool.name),
            arguments=arguments,
        )
        coroutine = getattr(tool, "coroutine", None)
        if coroutine is None:
            raise RuntimeError(f"platform tool {tool.name} is not asynchronous")
        result = await coroutine(runtime=runtime, **arguments)
        if not isinstance(result, tuple) or len(result) != 2:
            raise RuntimeError(f"platform tool {tool.name} returned an invalid result")
        content, artifact = result
        if not isinstance(artifact, dict):
            raise TypeError(f"platform tool {tool.name} returned no structured artifact")
        if artifact.get("status") == "error":
            error = artifact.get("error")
            error = error if isinstance(error, dict) else {}
            return _tool_error_result(
                code=str(error.get("code") or "platform_tool_failed"),
                message=str(
                    error.get("message") or content or "Platform tool failed"
                ),
                tool_name=str(tool.name),
            )
        events = _platform_events(context, str(tool.name))
        if events:
            artifact = dict(artifact)
            artifact["meta"] = {
                **dict(artifact.get("meta") or {}),
                "platform_events": events,
            }
        blocks: list[types.ContentBlock] = [
            types.TextContent(type="text", text=str(content))
        ]
        for item in list((artifact.get("meta") or {}).get("mcp_content") or []):
            if item.get("type") == "image":
                blocks.append(types.ImageContent(
                    type="image",
                    data=str(item.get("data") or ""),
                    mimeType=str(item.get("mime_type") or "image/png"),
                ))
        structured = artifact.get("structured_content")
        if structured is None and server == "diagram":
            try:
                parsed = json.loads(str(content))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"platform tool {tool.name} returned non-JSON content"
                ) from exc
            if not isinstance(parsed, dict):
                raise RuntimeError(
                    f"platform tool {tool.name} returned no structured object"
                )
            structured = parsed
        if structured is None:
            structured = artifact
        output_schema = diagram_output_schema(str(tool.name))
        if server == "diagram" and output_schema is not None:
            Draft202012Validator(output_schema).validate(structured)
        if server == "diagram" and str(tool.name) == "export_diagram":
            download_ref = structured.get("download_ref")
            if isinstance(download_ref, dict):
                download_path = str(download_ref.get("path") or "")
                download_revision = str(download_ref.get("revision") or "")
                if download_path.startswith("/data/exports/"):
                    blocks.append(types.ResourceLink(
                        type="resource_link",
                        name=download_path.rsplit("/", 1)[-1],
                        uri=(
                            f"vibecanvas://vfs{download_path}"
                            f"?revision={download_revision}"
                        ),
                        mimeType=str(
                            (structured.get("export") or {}).get(
                                "mime_type",
                                "application/octet-stream",
                            )
                        ),
                        size=int(
                            (structured.get("export") or {}).get("bytes", 0)
                        ),
                        annotations=types.Annotations(
                            audience=["user"],
                        ),
                    ))
        return blocks, structured
    finally:
        # MCP requests are long-lived async tasks. Never leak one tenant's sync
        # repository context into a later request reusing the same worker task.
        current_sync_tenant_id.reset(tenant_token)


def _build_server(server: str, tools: Iterable[Any]) -> FastMCP:
    tool_map = {str(tool.name): tool for tool in tools}
    origin = config.mcp.platform_internal_base_url
    mcp = FastMCP(
        f"vibecanvas-{server}",
        instructions=(
            "Built-in Skeinix platform capability. Tools are scoped to the "
            "authenticated Chat and active Agent Turn."
        ),
        token_verifier=_CapabilityVerifier(server),
        auth=AuthSettings(
            issuer_url=f"{origin}/",
            resource_server_url=platform_mcp_url(server),
            required_scopes=[f"platform:{server}"],
        ),
        streamable_http_path="/",
        stateless_http=True,
        transport_security=_platform_transport_security(origin),
        # Streamable HTTP's SSE response form is the SDK's mature path in the
        # pinned stable v1 release. The optional JSON-response mode currently
        # logs a ClosedResourceError after otherwise-successful stateless
        # requests, so do not enable that experimental optimization here.
        json_response=False,
    )
    lowlevel = mcp._mcp_server

    @lowlevel.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=name,
                description=str(getattr(tool, "description", "") or ""),
                inputSchema=_input_schema(tool),
                outputSchema=_output_schema(tool),
                annotations=_annotations(name),
            )
            for name, tool in tool_map.items()
        ]

    @lowlevel.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]):
        tool = tool_map.get(name)
        if tool is None:
            raise ValueError(f"unknown {server} tool: {name}")
        input_schema = _input_schema(tool)
        errors = sorted(
            Draft202012Validator(input_schema).iter_errors(arguments),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            error = errors[0]
            pointer = "/" + "/".join(
                str(item).replace("~", "~0").replace("/", "~1")
                for item in error.absolute_path
            )
            return _tool_error_result(
                code="invalid_tool_arguments",
                message=error.message,
                tool_name=name,
                json_pointer=pointer if pointer != "/" else None,
            )
        return await _invoke(tool, arguments, server)

    return mcp


# Resource discovery is always available. Workflow construction remains an
# explicit /build capability and deliberately omits the two read tools already
# exported by WORKFLOW_MCP, avoiding duplicate model-facing names.
WORKFLOW_MCP = _build_server("workflow", WORKFLOW_MCP_TOOLS)
TASK_MCP = _build_server("task", TASK_MCP_TOOLS)
DEPLOYMENT_MCP = _build_server("deployment", DEPLOYMENT_MCP_TOOLS)
KNOWLEDGE_MCP = _build_server("knowledge", KNOWLEDGE_MCP_TOOLS)
BUILD_MCP = _build_server(
    "build",
    [set_workflow, create_workflow, *BUILD_TOOLS, *RUN_TOOLS],
)
PLAN_MCP = _build_server("plan", PLAN_TOOLS)
DIAGRAM_MCP = _build_server("diagram", DIAGRAM_TOOLS)
CONFIG_MCP = _build_server("config", CONFIG_TOOLS)
INTERACTIVE_MCP = _build_server("interactive", INTERACTIVE_TOOLS)


_PLATFORM_MCP_TOOLSETS: dict[str, tuple[Any, ...]] = {
    "config": tuple(CONFIG_TOOLS),
    "interactive": tuple(INTERACTIVE_TOOLS),
    "workflow": tuple(WORKFLOW_MCP_TOOLS),
    "task": tuple(TASK_MCP_TOOLS),
    "deployment": tuple(DEPLOYMENT_MCP_TOOLS),
    "knowledge": tuple(KNOWLEDGE_MCP_TOOLS),
    "build": (set_workflow, create_workflow, *BUILD_TOOLS, *RUN_TOOLS),
    "plan": tuple(PLAN_TOOLS),
    "diagram": tuple(DIAGRAM_TOOLS),
}


def platform_mcp_catalog() -> list[dict[str, Any]]:
    """Describe exactly the Platform MCP services registered above.

    This is deliberately generated from the live tool objects instead of a
    second hard-coded frontend list.  It contains no internal endpoint or
    capability token and is therefore safe for an authenticated settings UI.
    """
    result: list[dict[str, Any]] = []
    for server, metadata in PLATFORM_MCP_METADATA.items():
        tools = _PLATFORM_MCP_TOOLSETS[server]
        result.append(
            {
                "id": server,
                **metadata,
                "tools": [
                    {
                        "name": str(tool.name),
                        "description": str(
                            getattr(tool, "description", "") or ""
                        ),
                        "input_schema": _input_schema(tool),
                        "output_schema": _output_schema(tool),
                        "annotations": _annotations(
                            str(tool.name)
                        ).model_dump(by_alias=True, exclude_none=True),
                    }
                    for tool in tools
                ],
            }
        )
    return result


def platform_mcp_apps() -> dict[str, Any]:
    return {
        "config": CONFIG_MCP.streamable_http_app(),
        "interactive": INTERACTIVE_MCP.streamable_http_app(),
        "workflow": WORKFLOW_MCP.streamable_http_app(),
        "task": TASK_MCP.streamable_http_app(),
        "deployment": DEPLOYMENT_MCP.streamable_http_app(),
        "knowledge": KNOWLEDGE_MCP.streamable_http_app(),
        "build": BUILD_MCP.streamable_http_app(),
        "plan": PLAN_MCP.streamable_http_app(),
        "diagram": DIAGRAM_MCP.streamable_http_app(),
    }


async def enter_platform_mcp_lifespans(stack) -> None:
    """Start official Streamable HTTP session managers in API lifespan."""
    # streamable_http_app() above initializes each manager before this hook.
    for server in (
        CONFIG_MCP,
        INTERACTIVE_MCP,
        WORKFLOW_MCP,
        TASK_MCP,
        DEPLOYMENT_MCP,
        KNOWLEDGE_MCP,
        BUILD_MCP,
        PLAN_MCP,
        DIAGRAM_MCP,
    ):
        await stack.enter_async_context(server.session_manager.run())
