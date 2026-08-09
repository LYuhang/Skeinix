"""Platform MCP tools for backend-owned Task and Deployment resources.

These adapters deliberately reuse the same route/service contracts as the web
application.  Authentication is reconstructed from the Turn-scoped Platform
MCP capability, while PostgreSQL RLS remains the authoritative tenant boundary.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Literal

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from langchain.tools import ToolRuntime
from langchain_core.tools import tool
from starlette.requests import Request

from vibecanvas_api.auth.deps import AuthContext
from vibecanvas_api.authorization.dependencies import (
    authz_service_for_session,
    scope_authz_service,
)
from vibecanvas_api.services.platform_mcp.authorization import (
    platform_auth_context,
)
from vibecanvas_api.routes import kb as kb_routes
from vibecanvas_api.routes import deployments as deployment_routes
from vibecanvas_api.routes import tasks as task_routes
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_kb import KbRepo


def _auth(runtime: ToolRuntime) -> AuthContext:
    return platform_auth_context(runtime.context)


def _request(runtime: ToolRuntime) -> Request:
    """Adapt Platform MCP invocation metadata to the shared HTTP policy seam."""
    context = runtime.context
    app = SimpleNamespace(
        state=SimpleNamespace(
            openfga_client=context.authorization_client,
        )
    )
    return Request({
        "type": "http",
        "method": "MCP",
        "path": "/api/internal/mcp/resource",
        "headers": [],
        "query_string": b"",
        "app": app,
        "state": {
            "request_id": f"platform-mcp:{context.turn_id}",
        },
    })


def _service(runtime: ToolRuntime, session):
    context = runtime.context
    service = authz_service_for_session(
        session=session,
        organization_id=str(context.tenant_id),
        openfga_client=context.authorization_client,
    )
    return scope_authz_service(
        service,
        session=session,
        auth=platform_auth_context(context),
    )


def _tool_result(tool_name: str, value: Any) -> tuple[str, dict[str, Any]]:
    encoded = jsonable_encoder(value)
    text = json.dumps(encoded, ensure_ascii=False, separators=(",", ":"))
    return text, {
        "schema_version": 1,
        "status": "success",
        "content": text,
        "content_type": "application/json",
        "content_abstract": f"{tool_name} completed",
        "ref": None,
        "payload": encoded,
        "meta": {"tool": tool_name},
    }


async def _route_call(awaitable):
    try:
        return await awaitable
    except HTTPException as exc:
        raise RuntimeError(str(exc.detail)) from exc


@tool(response_format="content_and_artifact")
async def list_knowledge_bases(
    *,
    runtime: ToolRuntime,
) -> str:
    """List knowledge bases visible to the current user.

    Results include a bounded discovery summary and backend-computed
    capabilities. Use the returned id with get_knowledge_base or
    search_knowledge; never guess an id.
    """
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            kb_routes.list_kbs(
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    catalog = []
    for item in result:
        value = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        value["virtual_root"] = f"/knowledge/{value['id']}"
        catalog.append(value)
    return _tool_result("list_knowledge_bases", catalog)


@tool(response_format="content_and_artifact")
async def get_knowledge_base(
    kb_id: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """Get one visible knowledge base and its current file/chunk counts."""
    try:
        parsed_id = uuid.UUID(kb_id)
    except ValueError as exc:
        raise ValueError(
            "kb_id must be a UUID returned by list_knowledge_bases"
        ) from exc
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            kb_routes.get_kb(
                parsed_id,
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("get_knowledge_base", result)


@tool(response_format="content_and_artifact")
async def list_knowledge_files(
    kb_id: str,
    *,
    runtime: ToolRuntime,
) -> str:
    """List the files in one authorized virtual Knowledge folder.

    Use the returned file_id and virtual_path with read_knowledge_file, or
    grep the folder with search_knowledge. Only metadata is disclosed here.
    """
    try:
        parsed_id = uuid.UUID(kb_id)
    except ValueError as exc:
        raise ValueError("kb_id must come from list_knowledge_bases") from exc
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(kb_routes.list_files(
            parsed_id,
            request=_request(runtime),
            file_status=None,
            ctx=_auth(runtime),
            session=session,
            service=_service(runtime, session),
        ))
    files = []
    for item in result:
        value = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        value["virtual_path"] = (
            f"/knowledge/{kb_id}/{value['id']}/{value['name']}"
        )
        files.append(value)
    return _tool_result("list_knowledge_files", {
        "virtual_root": f"/knowledge/{kb_id}",
        "files": files,
    })


@tool(response_format="content_and_artifact")
async def read_knowledge_file(
    kb_id: str,
    file_id: str,
    start_chunk: int = 0,
    max_chunks: int = 6,
    *,
    runtime: ToolRuntime,
) -> str:
    """Read a bounded page from a file's normalized text representation.

    Binary PDF and Office sources are exposed as parsed, read-only text. Call
    again with next_start_chunk when has_more is true. This avoids injecting an
    entire source into model context.
    """
    if start_chunk < 0:
        raise ValueError("start_chunk must be non-negative")
    if not 1 <= max_chunks <= 12:
        raise ValueError("max_chunks must be between 1 and 12")
    try:
        parsed_kb_id = uuid.UUID(kb_id)
        parsed_file_id = uuid.UUID(file_id)
    except ValueError as exc:
        raise ValueError("kb_id and file_id must come from Knowledge tools") from exc

    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        file, chunks = await KbRepo(session).read_file_chunks(
            kb_id=parsed_kb_id,
            file_id=parsed_file_id,
            offset=start_chunk,
            limit=max_chunks + 1,
        )
    if file is None:
        raise RuntimeError("knowledge_file_not_found_or_not_indexed")
    has_more = len(chunks) > max_chunks
    page = chunks[:max_chunks]
    next_start = start_chunk + len(page) if has_more else None
    return _tool_result("read_knowledge_file", {
        "virtual_path": f"/knowledge/{kb_id}/{file_id}/{file.name}",
        "file_id": file_id,
        "start_chunk": start_chunk,
        "next_start_chunk": next_start,
        "has_more": has_more,
        "chunks": [
            {
                "chunk_index": chunk.chunk_index,
                "text": chunk.text,
                "metadata": chunk.chunk_metadata or {},
            }
            for chunk in page
        ],
    })


@tool(response_format="content_and_artifact")
async def search_knowledge(
    kb_ids: list[str],
    query: str,
    top_k: int = 5,
    *,
    runtime: ToolRuntime,
) -> str:
    """Grep normalized source text in one or more authorized Knowledge folders.

    Obtain every kb_id from list_knowledge_bases first. The call fails closed
    if any requested knowledge base is unavailable or lacks use permission.
    """
    body = kb_routes.SearchRequest(
        kb_ids=kb_ids,
        query=query,
        top_k=top_k,
    )
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            kb_routes.search(
                body,
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("search_knowledge", result)


@tool(response_format="content_and_artifact")
async def task_list(
    status: list[str] | None = None,
    task_type: list[str] | None = None,
    workflow_id: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
    *,
    runtime: ToolRuntime,
) -> str:
    """List Task Center items visible to the current user.

    Use this before mutating a task to obtain its exact task id and current
    status. Results are newest first and include pagination metadata.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if offset < 0:
        raise ValueError("offset must be non-negative")
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await task_routes.list_tasks(
            request=_request(runtime),
            status=status or None,
            task_type=task_type or None,
            workflow_id=workflow_id,
            q=query,
            limit=limit,
            offset=offset,
            ctx=_auth(runtime),
            session=session,
            service=_service(runtime, session),
        )
        items = result["items"]
        total = result["total"]
        result["has_more"] = offset + len(items) < total
        result["next_offset"] = (
            offset + len(items)
            if offset + len(items) < total
            else None
        )
    return _tool_result("task_list", result)


@tool(response_format="content_and_artifact")
async def task_get(task_id: str, *, runtime: ToolRuntime) -> str:
    """Get one Task Center item by its exact task id."""
    context = runtime.context
    try:
        parsed_id = uuid.UUID(task_id)
    except ValueError as exc:
        raise ValueError("task_id must be a UUID returned by task_list") from exc
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(task_routes.get_task(
            parsed_id,
            request=_request(runtime),
            ctx=_auth(runtime),
            session=session,
            service=_service(runtime, session),
        ))
    return _tool_result("task_get", result)


@tool(response_format="content_and_artifact")
async def task_create_scheduled_run(
    name: str,
    workflow_id: str,
    schedule_type: Literal["interval", "cron"] = "interval",
    interval_seconds: int | None = None,
    cron_expr: str | None = None,
    timezone: str = "UTC",
    enabled: bool = True,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    input_preset: dict[str, Any] | None = None,
    mount_enabled: bool = False,
    notification_policy: dict[str, Any] | None = None,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Create a scheduled workflow Task.

    Set schedule_type="interval" with interval_seconds, or
    schedule_type="cron" with cron_expr. The workflow must already exist.
    This changes persistent platform state and requires user authorization by
    default.
    """
    del require_user_auth
    context = runtime.context
    body = task_routes.ScheduledRunCreateBody(
        name=name,
        workflow_id=workflow_id,
        enabled=enabled,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        cron_expr=cron_expr,
        timezone=timezone,
        start_at=start_at,
        end_at=end_at,
        input_preset=input_preset or {},
        mount_enabled=mount_enabled,
        notification_policy=notification_policy or {},
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            task_routes.create_scheduled_run(
                body,
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("task_create_scheduled_run", result)


@tool(response_format="content_and_artifact")
async def task_update_scheduled_run(
    task_id: str,
    name: str | None = None,
    enabled: bool | None = None,
    schedule_type: Literal["interval", "cron"] | None = None,
    interval_seconds: int | None = None,
    cron_expr: str | None = None,
    timezone: str | None = None,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    input_preset: dict[str, Any] | None = None,
    mount_enabled: bool | None = None,
    notification_policy: dict[str, Any] | None = None,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Update a scheduled workflow Task.

    Only provided fields are changed. Inspect the task with task_get first.
    This changes persistent platform state and requires user authorization by
    default.
    """
    del require_user_auth
    parsed_id = uuid.UUID(task_id)
    context = runtime.context
    body = task_routes.ScheduledRunPatchBody(
        name=name,
        enabled=enabled,
        schedule_type=schedule_type,
        interval_seconds=interval_seconds,
        cron_expr=cron_expr,
        timezone=timezone,
        start_at=start_at,
        end_at=end_at,
        input_preset=input_preset,
        mount_enabled=mount_enabled,
        notification_policy=notification_policy,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            task_routes.update_scheduled_run(
                parsed_id,
                body,
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("task_update_scheduled_run", result)


@tool(response_format="content_and_artifact")
async def task_delete_scheduled_run(
    task_id: str,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delete an inactive scheduled workflow Task.

    Active executions must be cancelled first. This is destructive and requires
    user authorization by default.
    """
    del require_user_auth
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            task_routes.delete_scheduled_run(
                uuid.UUID(task_id),
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("task_delete_scheduled_run", result)


@tool(response_format="content_and_artifact")
async def task_cancel(
    task_id: str,
    mode: Literal["soft", "force"] = "soft",
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Cancel a queued or running Task Center item.

    Prefer soft cancellation. Force cancellation may terminate active work and
    requires user authorization by default.
    """
    del require_user_auth
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            task_routes.cancel_task(
                uuid.UUID(task_id),
                task_routes.CancelBody(mode=mode),
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("task_cancel", result)


@tool(response_format="content_and_artifact")
async def task_resume(
    task_id: str,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Resume a resumable batch Task using its durable result checkpoint."""
    del require_user_auth
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            task_routes.resume_task(
                uuid.UUID(task_id),
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("task_resume", result)


@tool(response_format="content_and_artifact")
async def deployment_list(
    trigger_type: Literal["api", "webhook", "cron"] | None = None,
    enabled: bool | None = None,
    workflow_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    *,
    runtime: ToolRuntime,
) -> str:
    """List deployments, optionally filtered by trigger, status, or workflow."""
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await deployment_routes.list_deployments(
            request=_request(runtime),
            trigger_type=trigger_type,
            enabled=enabled,
            workflow_id=workflow_id,
            q=None,
            serving_only=False,
            limit=limit,
            offset=offset,
            ctx=_auth(runtime),
            session=session,
            service=_service(runtime, session),
        )
    items = result.get("items", [])
    result["has_more"] = len(items) == limit
    result["next_offset"] = offset + len(items) if len(items) == limit else None
    return _tool_result("deployment_list", result)


@tool(response_format="content_and_artifact")
async def deployment_get(deployment_id: str, *, runtime: ToolRuntime) -> str:
    """Get one deployment by the exact id returned from deployment_list."""
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            deployment_routes.get_deployment(
                uuid.UUID(deployment_id),
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("deployment_get", result)


@tool(response_format="content_and_artifact")
async def deployment_create(
    workflow_id: str,
    name: str,
    slug: str,
    trigger_type: Literal["api", "webhook", "cron"],
    version_pin: Literal["head", "specific"] = "head",
    pinned_major: int | None = None,
    pinned_sub: int | None = None,
    cron_expr: str | None = None,
    cron_timezone: str = "UTC",
    rate_limit_qps: int = 10,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Create a workflow deployment.

    API and webhook deployments return a one-time credential; preserve it in
    the response shown to the user. Cron deployments require cron_expr.
    Persistent creation requires user authorization by default.
    """
    del require_user_auth
    context = runtime.context
    body = deployment_routes.CreateDeploymentBody(
        wf_id=workflow_id,
        name=name,
        slug=slug,
        trigger_type=trigger_type,
        version_pin=version_pin,
        pinned_major=pinned_major,
        pinned_sub=pinned_sub,
        cron_expr=cron_expr,
        cron_tz=cron_timezone,
        rate_limit_qps=rate_limit_qps,
    )
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            deployment_routes.create_deployment(
                body,
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("deployment_create", result)


@tool(response_format="content_and_artifact")
async def deployment_update(
    deployment_id: str,
    name: str | None = None,
    enabled: bool | None = None,
    rate_limit_qps: int | None = None,
    cron_expr: str | None = None,
    cron_timezone: str | None = None,
    version_pin: Literal["head", "specific"] | None = None,
    pinned_major: int | None = None,
    pinned_sub: int | None = None,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Update mutable deployment settings.

    Workflow, trigger type, and slug are immutable. This changes persistent
    platform state and requires user authorization by default.
    """
    del require_user_auth
    context = runtime.context
    candidate_fields = {
        "name": name,
        "enabled": enabled,
        "rate_limit_qps": rate_limit_qps,
        "cron_expr": cron_expr,
        "cron_tz": cron_timezone,
        "version_pin": version_pin,
        "pinned_major": pinned_major,
        "pinned_sub": pinned_sub,
    }
    # Preserve HTTP PATCH semantics: an omitted MCP argument must not become an
    # explicit JSON null that clears a persistent column.
    body = deployment_routes.PatchDeploymentBody.model_validate({
        key: value for key, value in candidate_fields.items() if value is not None
    })
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(
            deployment_routes.patch_deployment(
                uuid.UUID(deployment_id),
                body,
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("deployment_update", result)


@tool(response_format="content_and_artifact")
async def deployment_delete(
    deployment_id: str,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Soft-delete and disable a deployment.

    This is destructive and requires user authorization by default.
    """
    del require_user_auth
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        await _route_call(
            deployment_routes.delete_deployment(
                uuid.UUID(deployment_id),
                request=_request(runtime),
                ctx=_auth(runtime),
                session=session,
                service=_service(runtime, session),
            )
        )
    return _tool_result("deployment_delete", {"status": "deleted"})


TASK_MCP_TOOLS = [
    task_list,
    task_get,
    task_create_scheduled_run,
    task_update_scheduled_run,
    task_delete_scheduled_run,
    task_cancel,
    task_resume,
]

DEPLOYMENT_MCP_TOOLS = [
    deployment_list,
    deployment_get,
    deployment_create,
    deployment_update,
    deployment_delete,
]

KNOWLEDGE_MCP_TOOLS = [
    list_knowledge_bases,
    get_knowledge_base,
    list_knowledge_files,
    search_knowledge,
    read_knowledge_file,
]
