"""Host-side authorization boundary shared by Platform MCP workflow tools."""

from __future__ import annotations

from dataclasses import dataclass
import uuid
from typing import Any

from vibecanvas_api.agents.tools.decorator import ToolError
from vibecanvas_api.auth.deps import AuthContext
from vibecanvas_api.authorization.dependencies import (
    authz_service_for_session,
    scope_authz_service,
)
from vibecanvas_api.authorization.openfga_client import (
    OpenFgaUnavailableError,
)
from vibecanvas_api.authorization.projection import (
    apply_committed_structural_mutations,
    enqueue_structural_delta,
    resource_root_edges,
)
from vibecanvas_api.authorization.mutations import (
    AuthzMutationCoordinator,
)
from vibecanvas_api.authorization.service import (
    AuthzService,
    batch_resource_decisions,
)
from vibecanvas_api.authorization.types import (
    Action,
    AuthzRequestContext,
    ConsistencyPreference,
    Decision,
    PrincipalRef,
    PrincipalType,
    ResourceRef,
    ResourceType,
)
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


WORKFLOW_TOOL_ACTIONS: dict[str, Action] = {
    "list_workflows": Action.VIEW_METADATA,
    "get_workflow": Action.VIEW,
    "set_workflow": Action.USE,
    "create_workflow": Action.CREATE,
    "get_node_spec": Action.VIEW,
    "check_workflow": Action.VIEW,
    "update_canvas": Action.UPDATE,
    "new_version": Action.UPDATE,
    "run_workflow": Action.EXECUTE,
    "node_execute": Action.EXECUTE,
    "batch_execute": Action.EXECUTE,
}

TASK_TOOL_ACTIONS: dict[str, Action] = {
    "task_list": Action.VIEW_METADATA,
    "task_get": Action.VIEW,
    "task_create_scheduled_run": Action.CREATE,
    "task_update_scheduled_run": Action.UPDATE,
    "task_delete_scheduled_run": Action.DELETE,
    "task_cancel": Action.CANCEL,
    "task_resume": Action.RESUME,
}

DEPLOYMENT_TOOL_ACTIONS: dict[str, Action] = {
    "deployment_list": Action.VIEW_METADATA,
    "deployment_get": Action.VIEW,
    "deployment_create": Action.DEPLOY,
    "deployment_update": Action.UPDATE,
    "deployment_delete": Action.DELETE,
}

KNOWLEDGE_TOOL_ACTIONS: dict[str, Action] = {
    "knowledge_list": Action.VIEW_METADATA,
    "knowledge_get": Action.USE,
    "knowledge_create": Action.CREATE,
    "knowledge_update": Action.UPDATE,
    "knowledge_delete": Action.DELETE,
    "knowledge_search": Action.USE,
}


@dataclass(frozen=True, slots=True)
class AuthorizedWorkflowSnapshot:
    meta: dict[str, Any]
    workflow: dict[str, Any]
    decision: Decision


def platform_mcp_tool_action(tool_name: str) -> Action:
    """Return the canonical workflow action used by manifest and runtime."""
    try:
        return WORKFLOW_TOOL_ACTIONS[tool_name]
    except KeyError as exc:
        raise ValueError(
            f"unregistered Platform MCP workflow tool: {tool_name}"
        ) from exc


def platform_resource_tool_action(server: str, tool_name: str) -> Action:
    if server == "task":
        actions = TASK_TOOL_ACTIONS
    elif server == "deployment":
        actions = DEPLOYMENT_TOOL_ACTIONS
    elif server == "knowledge":
        actions = KNOWLEDGE_TOOL_ACTIONS
    else:
        raise ValueError(f"unsupported Platform MCP resource server: {server}")
    try:
        return actions[tool_name]
    except KeyError as exc:
        raise ValueError(
            f"unregistered Platform MCP {server} tool: {tool_name}"
        ) from exc


def _principal(ctx) -> PrincipalRef:
    user_id = str(getattr(ctx, "username", "") or "").strip()
    if not user_id:
        raise ToolError(
            "permission_denied",
            "The resource is unavailable or access is denied.",
        )
    return PrincipalRef(PrincipalType.USER, user_id)


def _request_context(
    ctx,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> AuthzRequestContext:
    organization_id = str(getattr(ctx, "tenant_id", "") or "").strip()
    return AuthzRequestContext(
        active_organization_id=organization_id,
        request_id=f"platform-mcp:{getattr(ctx, 'turn_id', '')}",
        session_id=str(
            getattr(ctx, "authorization_session_id", "") or ""
        ),
        session_generation=int(
            getattr(ctx, "authorization_session_generation", 0) or 0
        ),
        membership_id=str(
            getattr(ctx, "authorization_membership_id", "") or ""
        ),
        membership_role=str(
            getattr(ctx, "authorization_membership_role", "") or ""
        ),
        membership_status=str(
            getattr(ctx, "authorization_membership_status", "") or ""
        ),
        authentication_strength=str(
            getattr(
                ctx,
                "authorization_authentication_strength",
                "",
            )
            or ""
        ),
        consistency=consistency,
    )


def platform_auth_context(ctx) -> AuthContext:
    return AuthContext(
        user_id=str(getattr(ctx, "username", "") or ""),
        tenant_id=str(getattr(ctx, "tenant_id", "") or ""),
        email="",
        active_organization_id=str(getattr(ctx, "tenant_id", "") or ""),
        membership_id=str(
            getattr(ctx, "authorization_membership_id", "") or ""
        ),
        membership_role=str(
            getattr(ctx, "authorization_membership_role", "") or ""
        ),
        membership_status=str(
            getattr(ctx, "authorization_membership_status", "") or ""
        ),
        session_generation=int(
            getattr(ctx, "authorization_session_generation", 0) or 0
        ),
        authentication_strength=str(
            getattr(ctx, "authorization_authentication_strength", "") or ""
        ),
        session_id=str(getattr(ctx, "authorization_session_id", "") or ""),
        session_audience=str(
            getattr(ctx, "authorization_session_audience", "web") or "web"
        ),
        privileged_access_request_id=str(
            getattr(
                ctx,
                "authorization_privileged_access_request_id",
                "",
            )
            or ""
        ),
        privileged_resource_type=str(
            getattr(ctx, "authorization_privileged_resource_type", "") or ""
        ),
        privileged_resource_id=str(
            getattr(ctx, "authorization_privileged_resource_id", "") or ""
        ),
        privileged_actions=frozenset(
            getattr(ctx, "authorization_privileged_actions", ()) or ()
        ),
        privileged_expires_at=getattr(
            ctx,
            "authorization_privileged_expires_at",
            None,
        ),
    )


def _workflow_resource(ctx, workflow_id: str) -> ResourceRef:
    return ResourceRef(
        ResourceType.WORKFLOW,
        workflow_id,
        str(getattr(ctx, "tenant_id", "") or ""),
    )


def _chat_resource(ctx) -> ResourceRef:
    return ResourceRef(
        ResourceType.CHAT,
        str(getattr(ctx, "chat_id", "") or ""),
        str(getattr(ctx, "tenant_id", "") or ""),
    )


def _task_resource(ctx, task_id: str) -> ResourceRef:
    return ResourceRef(
        ResourceType.TASK,
        task_id,
        str(getattr(ctx, "tenant_id", "") or ""),
    )


def _deployment_resource(ctx, deployment_id: str) -> ResourceRef:
    return ResourceRef(
        ResourceType.DEPLOYMENT,
        deployment_id,
        str(getattr(ctx, "tenant_id", "") or ""),
    )


def _knowledge_base_resource(
    ctx,
    knowledge_base_id: str,
) -> ResourceRef:
    return ResourceRef(
        ResourceType.KNOWLEDGE_BASE,
        knowledge_base_id,
        str(getattr(ctx, "tenant_id", "") or ""),
    )


def _service(ctx, session) -> AuthzService:
    service = authz_service_for_session(
        session=session,
        organization_id=str(getattr(ctx, "tenant_id", "") or ""),
        openfga_client=getattr(ctx, "authorization_client", None),
    )
    return scope_authz_service(
        service,
        session=session,
        auth=platform_auth_context(ctx),
    )


def _permission_error() -> ToolError:
    # Preserve the same non-enumerating contract as direct HTTP object routes.
    return ToolError(
        "permission_denied",
        "The resource is unavailable or access is denied.",
    )


async def _decision(
    *,
    ctx,
    service: AuthzService,
    action: Action,
    resource: ResourceRef,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> Decision:
    try:
        decision = await service.check(
            _principal(ctx),
            action,
            resource,
            _request_context(ctx, consistency=consistency),
        )
    except OpenFgaUnavailableError as exc:
        raise ToolError(
            "authorization_unavailable",
            "Authorization is temporarily unavailable.",
        ) from exc
    if not decision.allowed:
        raise _permission_error()
    return decision


async def require_organization_create(ctx) -> Decision:
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        return await _decision(
            ctx=ctx,
            service=_service(ctx, session),
            action=Action.CREATE,
            resource=ResourceRef(
                ResourceType.ORGANIZATION,
                organization_id,
                organization_id,
            ),
        )


async def require_workflow_action(
    ctx,
    workflow_id: str,
    action: Action,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> Decision:
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        raise ToolError("no_workflow", "No workflow is selected for this chat.")
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        return await _decision(
            ctx=ctx,
            service=_service(ctx, session),
            action=action,
            resource=_workflow_resource(ctx, workflow_id),
            consistency=consistency,
        )


async def require_chat_action(
    ctx,
    action: Action,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> Decision:
    """Authorize a Chat-owned workspace resource at the current membership."""
    chat_id = str(getattr(ctx, "chat_id", "") or "").strip()
    if not chat_id:
        raise _permission_error()
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        return await _decision(
            ctx=ctx,
            service=_service(ctx, session),
            action=action,
            resource=_chat_resource(ctx),
            consistency=consistency,
        )


async def require_task_action(
    ctx,
    task_id: str,
    action: Action,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> Decision:
    task_id = str(task_id or "").strip()
    try:
        uuid.UUID(task_id)
    except ValueError as exc:
        raise _permission_error() from exc
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        return await _decision(
            ctx=ctx,
            service=_service(ctx, session),
            action=action,
            resource=_task_resource(ctx, task_id),
            consistency=consistency,
        )


async def require_deployment_action(
    ctx,
    deployment_id: str,
    action: Action,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> Decision:
    deployment_id = str(deployment_id or "").strip()
    try:
        uuid.UUID(deployment_id)
    except ValueError as exc:
        raise _permission_error() from exc
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        return await _decision(
            ctx=ctx,
            service=_service(ctx, session),
            action=action,
            resource=_deployment_resource(ctx, deployment_id),
            consistency=consistency,
        )


async def require_knowledge_base_action(
    ctx,
    knowledge_base_id: str,
    action: Action,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.MINIMIZE_LATENCY
    ),
) -> Decision:
    knowledge_base_id = str(knowledge_base_id or "").strip()
    try:
        uuid.UUID(knowledge_base_id)
    except ValueError as exc:
        raise _permission_error() from exc
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        return await _decision(
            ctx=ctx,
            service=_service(ctx, session),
            action=action,
            resource=_knowledge_base_resource(
                ctx,
                knowledge_base_id,
            ),
            consistency=consistency,
        )


async def recheck_platform_workflow_action(
    ctx,
    workflow_id: str,
    action: Action,
    *,
    consistency: ConsistencyPreference = (
        ConsistencyPreference.HIGHER_CONSISTENCY
    ),
) -> Decision | None:
    """Re-check immediately before side effects on real Platform MCP calls.

    Pure internal/unit invocations do not cross the Platform MCP authorization
    boundary; production contexts are always stamped ``platform_mcp`` by the
    server and therefore cannot skip this check.
    """
    if getattr(ctx, "runtime_location", "") != "platform_mcp":
        return None
    return await require_workflow_action(
        ctx,
        workflow_id,
        action,
        consistency=consistency,
    )


async def load_authorized_workflow(
    ctx,
    workflow_id: str,
    action: Action,
) -> AuthorizedWorkflowSnapshot:
    """Authorize before reading either metadata or workflow content."""
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        raise ToolError("no_workflow", "No workflow is selected for this chat.")
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    async with session_scope(tenant_id=organization_id) as session:
        service = _service(ctx, session)
        decision = await _decision(
            ctx=ctx,
            service=service,
            action=action,
            resource=_workflow_resource(ctx, workflow_id),
        )
        repo = WorkflowRepo(session, str(getattr(ctx, "username", "") or ""))
        meta = await repo.get_meta(workflow_id)
        if not meta:
            raise _permission_error()
        workflow = await repo.get_current_workflow(workflow_id)
        return AuthorizedWorkflowSnapshot(meta, workflow, decision)


async def list_authorized_workflows(ctx) -> list[dict[str, Any]]:
    """List only metadata-authorized rows and attach effective capabilities."""
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    principal = _principal(ctx)
    context = _request_context(ctx)
    async with session_scope(tenant_id=organization_id) as session:
        service = _service(ctx, session)
        try:
            authorized_ids = await service.list_authorized_ids(
                principal,
                Action.VIEW_METADATA,
                ResourceType.WORKFLOW,
                context,
            )
        except OpenFgaUnavailableError as exc:
            raise ToolError(
                "authorization_unavailable",
                "Authorization is temporarily unavailable.",
            ) from exc
        repo = WorkflowRepo(session, principal.id)
        rows, _total = await repo.list_authorized_workflows(
            authorized_ids,
            limit=1000,
            offset=0,
        )
        resources = [
            _workflow_resource(ctx, str(row["wf_id"])) for row in rows
        ]
        try:
            decisions = await batch_resource_decisions(
                service,
                principal=principal,
                resources=resources,
                context=context,
            )
        except OpenFgaUnavailableError as exc:
            raise ToolError(
                "authorization_unavailable",
                "Authorization is temporarily unavailable.",
            ) from exc
        return [
            {
                **row,
                "access": {
                    "capabilities": sorted(
                        action.value
                        for action in decisions[resource].capabilities
                    ),
                    "effective_role": decisions[resource].effective_role,
                    "source": "computed",
                },
            }
            for row, resource in zip(rows, resources, strict=True)
        ]


async def create_authorized_workflow(
    ctx,
    *,
    name: str,
    description: str,
) -> AuthorizedWorkflowSnapshot:
    """Create a workflow and its structural relationships atomically."""
    organization_id = str(getattr(ctx, "tenant_id", "") or "")
    principal = _principal(ctx)
    async with session_scope(tenant_id=organization_id) as session:
        service = _service(ctx, session)
        await _decision(
            ctx=ctx,
            service=service,
            action=Action.CREATE,
            resource=ResourceRef(
                ResourceType.ORGANIZATION,
                organization_id,
                organization_id,
            ),
        )
        repo = WorkflowRepo(session, principal.id)
        meta = await repo.create_workflow(
            name=name,
            description=description,
            creator_user_id=principal.id,
        )
        coordinator = AuthzMutationCoordinator(
            client=getattr(ctx, "authorization_client", None),
            organization_id=organization_id,
        )
        mutation_ids = await enqueue_structural_delta(
            session=session,
            coordinator=coordinator,
            actor_type="user",
            actor_id=principal.id,
            before=frozenset(),
            after=resource_root_edges(
                organization_id=organization_id,
                object_type="workflow",
                object_id=str(meta["wf_id"]),
                owner_relation="manager",
                owner_type="user",
                owner_id=principal.id,
            ),
            operation_id=uuid.uuid4().hex,
            source="platform-mcp-workflow-create",
        )
        await session.commit()

    try:
        await apply_committed_structural_mutations(
            coordinator,
            mutation_ids,
        )
    except OpenFgaUnavailableError as exc:
        raise ToolError(
            "authorization_unavailable",
            "Authorization is temporarily unavailable.",
        ) from exc

    return await load_authorized_workflow(
        ctx,
        str(meta["wf_id"]),
        Action.VIEW,
    )


async def prepare_platform_workflow_tool(
    ctx,
    *,
    server: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Authorize one concrete MCP tool call before its implementation runs."""
    if server not in {"workflow", "build"}:
        return
    action = platform_mcp_tool_action(tool_name)
    if tool_name == "list_workflows":
        # The implementation performs authorization-aware filtering.
        return
    if tool_name == "create_workflow":
        await require_organization_create(ctx)
        return

    workflow_id = (
        str(arguments.get("workflow_id") or "").strip()
        if tool_name == "set_workflow"
        else str(getattr(ctx, "current_workflow_id", "") or "").strip()
    )
    snapshot = await load_authorized_workflow(
        ctx,
        workflow_id,
        action,
    )
    # Build/run tools operate on this in-memory snapshot. It is populated only
    # after the requested action has been authorized.
    if server == "build" and tool_name != "set_workflow":
        ctx.workflow = snapshot.workflow


async def prepare_platform_tool(
    ctx,
    *,
    server: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    """Authorize the concrete Platform MCP operation before tool execution."""
    if server in {"workflow", "build"}:
        await prepare_platform_workflow_tool(
            ctx,
            server=server,
            tool_name=tool_name,
            arguments=arguments,
        )
        return
    if server not in {"task", "deployment", "knowledge"}:
        return
    action = platform_resource_tool_action(server, tool_name)
    if tool_name in {
        "task_list",
        "deployment_list",
        "knowledge_list",
    }:
        # The implementation performs ListObjects + tenant SQL intersection.
        return
    if tool_name == "task_create_scheduled_run":
        await require_organization_create(ctx)
        await require_workflow_action(
            ctx,
            str(arguments.get("workflow_id") or ""),
            Action.USE,
        )
        return
    if tool_name == "deployment_create":
        await require_organization_create(ctx)
        await require_workflow_action(
            ctx,
            str(arguments.get("workflow_id") or ""),
            Action.DEPLOY,
        )
        return
    if tool_name == "knowledge_create":
        await require_organization_create(ctx)
        return
    if tool_name == "knowledge_search":
        knowledge_base_ids = arguments.get("kb_ids")
        if not isinstance(knowledge_base_ids, list) or not knowledge_base_ids:
            raise _permission_error()
        for knowledge_base_id in knowledge_base_ids:
            await require_knowledge_base_action(
                ctx,
                str(knowledge_base_id),
                action,
            )
        return
    if server == "task":
        await require_task_action(
            ctx,
            str(arguments.get("task_id") or ""),
            action,
        )
    elif server == "deployment":
        await require_deployment_action(
            ctx,
            str(arguments.get("deployment_id") or ""),
            action,
        )
    else:
        await require_knowledge_base_action(
            ctx,
            str(arguments.get("kb_id") or ""),
            action,
        )


__all__ = [
    "AuthorizedWorkflowSnapshot",
    "DEPLOYMENT_TOOL_ACTIONS",
    "KNOWLEDGE_TOOL_ACTIONS",
    "TASK_TOOL_ACTIONS",
    "WORKFLOW_TOOL_ACTIONS",
    "create_authorized_workflow",
    "list_authorized_workflows",
    "load_authorized_workflow",
    "platform_mcp_tool_action",
    "platform_resource_tool_action",
    "prepare_platform_tool",
    "prepare_platform_workflow_tool",
    "recheck_platform_workflow_action",
    "require_organization_create",
    "require_deployment_action",
    "require_knowledge_base_action",
    "require_chat_action",
    "require_task_action",
    "require_workflow_action",
]
