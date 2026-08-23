"""Platform MCP tools for backend-owned Task and Deployment resources.

These adapters deliberately reuse the same route/service contracts as the web
application.  Authentication is reconstructed from the Turn-scoped Platform
MCP capability, while PostgreSQL RLS remains the authoritative tenant boundary.
"""

from __future__ import annotations

import json
import posixpath
import re
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
from vibecanvas_api.services.knowledge_packages import (
    PackageFile,
    enqueue_package_indexing,
    package_snapshot,
    replace_package,
    validate_package,
)
from vibecanvas_api.services.file_format import content_type_for


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
        "path": "/internal/platform-capability/resource",
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


def _knowledge_destination(value: str, fallback: str) -> str:
    path = str(value or fallback).rstrip("/")
    if not (path.startswith("/data/") or path.startswith("/mount/")):
        raise ValueError("destination/source path must be under /data or /mount")
    if any(part in {"", ".", ".."} for part in path[1:].split("/")):
        raise ValueError("destination/source path contains an invalid segment")
    return path


async def _collect_package(runtime: ToolRuntime, source_path: str) -> list[PackageFile]:
    root = _knowledge_destination(source_path, "/data/knowledge-package")
    sandbox = await runtime.context.sandbox_session()
    pending = [(root, "")]
    result: list[PackageFile] = []
    while pending:
        absolute, relative = pending.pop()
        listing = await sandbox.list_dir(absolute)
        if not listing.get("ok"):
            raise RuntimeError(
                f"could not list Knowledge package path {absolute!r}: "
                f"{listing.get('error') or 'unknown error'}"
            )
        for entry in listing.get("entries") or []:
            name = str(entry.get("name") or "")
            if not name or "/" in name or name in {".", ".."}:
                raise RuntimeError("sandbox returned an invalid package entry")
            child_absolute = posixpath.join(absolute, name)
            child_relative = posixpath.join(relative, name) if relative else name
            if entry.get("is_dir"):
                pending.append((child_absolute, child_relative))
                continue
            loaded = await sandbox.read_bytes(child_absolute)
            if not loaded.get("ok") or not isinstance(loaded.get("data"), bytes):
                raise RuntimeError(
                    f"could not read Knowledge package file {child_absolute!r}: "
                    f"{loaded.get('error') or 'unknown error'}"
                )
            result.append(PackageFile(
                path=child_relative,
                data=loaded["data"],
                content_type=content_type_for(child_relative, loaded["data"]),
            ))
    # Validate the complete snapshot before a create route can commit a
    # Knowledge resource.  ``replace_package`` validates again at the storage
    # boundary, but doing it here prevents an invalid sandbox directory from
    # leaving behind a committed, half-created package.
    return validate_package(result)


@tool("knowledge_list", response_format="content_and_artifact")
async def knowledge_list(*, runtime: ToolRuntime) -> str:
    """List authorized Knowledge packages and their current package versions."""
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        rows = await _route_call(kb_routes.list_kbs(
            request=_request(runtime),
            ctx=_auth(runtime),
            session=session,
            service=_service(runtime, session),
        ))
    return _tool_result("knowledge_list", rows)


@tool("knowledge_get", response_format="content_and_artifact")
async def knowledge_get(
    kb_id: str,
    destination_path: str = "",
    *,
    runtime: ToolRuntime,
) -> str:
    """Materialize an authorized Knowledge package into this Chat sandbox.

    Returns its local directory, package version, and README entry point. Read
    and search the local files with ordinary Agent filesystem tools.
    """
    parsed_id = uuid.UUID(kb_id)
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        await kb_routes._authorize_knowledge_base(
            request=_request(runtime), ctx=_auth(runtime),
            service=_service(runtime, session), knowledge_base_id=parsed_id,
            action=kb_routes.Action.USE,
        )
        repo = KbRepo(session)
        kb = await repo.get_active(parsed_id)
        if kb is None:
            raise RuntimeError("knowledge_not_found")
        files = await package_snapshot(session, parsed_id)
        package_version = kb.package_version
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", kb.name).strip("-.") or kb_id
    root = _knowledge_destination(
        destination_path,
        f"/data/knowledge/{safe_name}-v{package_version}",
    )
    sandbox = await context.sandbox_session()
    for item in files:
        written = await sandbox.write_bytes(posixpath.join(root, item.path), item.data)
        if not written.get("ok"):
            raise RuntimeError(
                f"could not materialize {item.path!r}: "
                f"{written.get('error') or 'unknown error'}"
            )
    return _tool_result("knowledge_get", {
        "id": kb_id,
        "local_directory": root,
        "package_version": package_version,
        "readme": f"{root}/README.md",
        "file_count": len(files),
    })


@tool("knowledge_create", response_format="content_and_artifact")
async def knowledge_create(
    name: str,
    source_path: str,
    description: str = "",
    *,
    runtime: ToolRuntime,
) -> str:
    """Publish an Agent-prepared local directory as a Knowledge package.

    The directory must contain a root README.md. Validate the complete local
    package before calling this persistent mutation.
    """
    files = await _collect_package(runtime, source_path)
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        created = await _route_call(kb_routes.create_kb(
            kb_routes.KbCreate(name=name, description=description or None),
            request=_request(runtime), ctx=_auth(runtime), session=session,
            service=_service(runtime, session),
        ))
        kb_id = uuid.UUID(created.id)
        version, pending = await replace_package(
            session, kb_id=kb_id, actor_user_id=uuid.UUID(_auth(runtime).user_id),
            expected_version=created.package_version, files=files,
            increment_version=False,
        )
        await session.commit()
    await enqueue_package_indexing(
        tenant_id=str(context.tenant_id), user_id=_auth(runtime).user_id,
        file_ids=pending,
    )
    return _tool_result("knowledge_create", {
        "id": str(kb_id), "name": name, "package_version": version,
        "file_count": len(files), "source_path": source_path,
    })


@tool("knowledge_update", response_format="content_and_artifact")
async def knowledge_update(
    kb_id: str,
    source_path: str,
    expected_version: int,
    *,
    runtime: ToolRuntime,
) -> str:
    """Publish a validated local directory as a new package version.

    ``expected_version`` must be the value returned by knowledge_get. A stale
    version fails without overwriting another user's newer changes.
    """
    files = await _collect_package(runtime, source_path)
    parsed_id = uuid.UUID(kb_id)
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        await kb_routes._authorize_knowledge_base(
            request=_request(runtime), ctx=_auth(runtime),
            service=_service(runtime, session), knowledge_base_id=parsed_id,
            action=kb_routes.Action.UPDATE,
        )
        try:
            version, pending = await replace_package(
                session, kb_id=parsed_id,
                actor_user_id=uuid.UUID(_auth(runtime).user_id),
                expected_version=expected_version, files=files,
            )
        except RuntimeError as exc:
            if str(exc).startswith("knowledge_version_conflict:"):
                current = str(exc).split(":", 1)[1]
                raise RuntimeError(
                    f"knowledge_version_conflict: expected {expected_version}, "
                    f"current {current}; call knowledge_get and reconcile first"
                ) from exc
            raise
        await session.commit()
    await enqueue_package_indexing(
        tenant_id=str(context.tenant_id), user_id=_auth(runtime).user_id,
        file_ids=pending,
    )
    return _tool_result("knowledge_update", {
        "id": kb_id, "package_version": version,
        "file_count": len(files), "source_path": source_path,
    })


@tool("knowledge_delete", response_format="content_and_artifact")
async def knowledge_delete(
    kb_id: str,
    confirm: bool,
    *,
    runtime: ToolRuntime,
) -> str:
    """Delete a Knowledge package only after the user explicitly requested it."""
    if not confirm:
        raise ValueError("confirm must be true after explicit user intent")
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        await _route_call(kb_routes.delete_kb(
            uuid.UUID(kb_id), request=_request(runtime), ctx=_auth(runtime),
            session=session, service=_service(runtime, session),
        ))
    return _tool_result("knowledge_delete", {"id": kb_id, "status": "deleted"})


@tool("knowledge_search", response_format="content_and_artifact")
async def knowledge_search(
    kb_ids: list[str], query: str, top_k: int = 5, *, runtime: ToolRuntime,
) -> str:
    """Search the derived text index of selected authorized packages."""
    body = kb_routes.SearchRequest(kb_ids=kb_ids, query=query, top_k=top_k)
    context = runtime.context
    async with session_scope(tenant_id=str(context.tenant_id)) as session:
        result = await _route_call(kb_routes.search(
            body, request=_request(runtime), ctx=_auth(runtime), session=session,
            service=_service(runtime, session),
        ))
    return _tool_result("knowledge_search", result)


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
    trigger_type: Literal["api", "webhook"] | None = None,
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
    trigger_type: Literal["api", "webhook"],
    version_pin: Literal["head", "specific"] = "head",
    pinned_major: int | None = None,
    pinned_sub: int | None = None,
    rate_limit_qps: int = 10,
    require_user_auth: bool = True,
    *,
    runtime: ToolRuntime,
) -> str:
    """Create a workflow deployment.

    API and webhook deployments return a one-time credential; preserve it in
    the response shown to the user.
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
    knowledge_list,
    knowledge_get,
    knowledge_create,
    knowledge_update,
    knowledge_delete,
    knowledge_search,
]
