"""Durable product API for Dynamic Execution Plan previews and controls."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth.deps import AuthContext, current_user, tenant_db
from ..authorization.dependencies import authorize_resource, get_authz_service
from ..authorization.service import AuthzService
from ..authorization.stream_guard import authorization_lease_is_valid
from ..authorization.types import Action, ResourceRef, ResourceType
from ..schemas.execution_plan import (
    ExecutionNodeRunOut,
    ExecutionPlanCardOut,
    ExecutionPlanControlBody,
    ExecutionPlanDetailOut,
    ExecutionPlanRunOut,
)
from ..services.execution_plans.projection import ExecutionPlanProjectionService
from ..storage.db import session_scope
from ..storage.execution_plan_repo import ExecutionPlanRepo
from ..streaming.sse import format_event


router = APIRouter(prefix="/api/v1", tags=["execution-plans"])
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


async def _authorize_chat(
    *, request: Request, auth: AuthContext, service: AuthzService,
    chat_id: str, action: Action,
) -> None:
    await authorize_resource(
        request=request,
        auth=auth,
        service=service,
        resource=ResourceRef(ResourceType.CHAT, chat_id, auth.active_organization_id),
        action=action,
    )


@router.get("/execution-plans", response_model=list[ExecutionPlanCardOut])
async def list_execution_plans(
    request: Request,
    chat_id: str = Query(min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=chat_id, action=Action.VIEW,
    )
    rows = await ExecutionPlanProjectionService(
        session, user_id=auth.user_id,
    ).list_plans(chat_id=chat_id, limit=limit)
    return [ExecutionPlanCardOut.model_validate(row) for row in rows]


@router.get("/execution-plans/{plan_id}", response_model=ExecutionPlanDetailOut)
async def get_execution_plan(
    plan_id: str,
    request: Request,
    revision: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projection = await ExecutionPlanProjectionService(
        session, user_id=auth.user_id,
    ).get_plan(plan_id=plan_id, revision=revision)
    if projection is None:
        raise HTTPException(status_code=404, detail="execution_plan_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=projection["chat_id"], action=Action.VIEW,
    )
    return ExecutionPlanDetailOut.model_validate(projection)


@router.get("/execution-plan-runs/{plan_run_id}", response_model=ExecutionPlanRunOut)
async def get_execution_plan_run(
    plan_run_id: str,
    request: Request,
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projection = await ExecutionPlanProjectionService(
        session, user_id=auth.user_id,
    ).get_run(plan_run_id=plan_run_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="execution_plan_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=projection["chat_id"], action=Action.VIEW,
    )
    return ExecutionPlanRunOut.model_validate(projection)


@router.get("/execution-node-runs/{node_run_id}", response_model=ExecutionNodeRunOut)
async def get_execution_node_run(
    node_run_id: str,
    request: Request,
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projection = await ExecutionPlanProjectionService(
        session, user_id=auth.user_id,
    ).get_node(node_run_id=node_run_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="execution_node_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=projection["chat_id"], action=Action.VIEW,
    )
    return ExecutionNodeRunOut.model_validate(projection)


@router.get("/execution-node-runs/{node_run_id}/output")
async def get_execution_node_output(
    node_run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projection = await ExecutionPlanProjectionService(
        session, user_id=auth.user_id,
    ).list_node_output(node_run_id=node_run_id, after=after, limit=limit)
    if projection is None:
        raise HTTPException(status_code=404, detail="execution_node_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=projection["chat_id"], action=Action.VIEW,
    )
    return {
        "items": projection["items"],
        "last_output_seq": projection["last_output_seq"],
        "has_more": projection["has_more"],
    }


@router.post("/execution-plan-runs/{plan_run_id}/cancel", response_model=ExecutionPlanRunOut)
async def cancel_execution_plan_run(
    plan_run_id: str,
    body: ExecutionPlanControlBody,
    request: Request,
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projector = ExecutionPlanProjectionService(session, user_id=auth.user_id)
    before = await projector.get_run(plan_run_id=plan_run_id)
    if before is None:
        raise HTTPException(status_code=404, detail="execution_plan_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=before["chat_id"], action=Action.CANCEL,
    )
    try:
        await ExecutionPlanRepo(session).request_cancel(
            plan_run_id=plan_run_id,
            actor_id=auth.user_id,
            actor_type="user",
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    projection = await projector.get_run(plan_run_id=plan_run_id)
    return ExecutionPlanRunOut.model_validate(projection)


@router.post("/execution-node-runs/{node_run_id}/cancel", response_model=ExecutionNodeRunOut)
async def cancel_execution_node_run(
    node_run_id: str,
    body: ExecutionPlanControlBody,
    request: Request,
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projector = ExecutionPlanProjectionService(session, user_id=auth.user_id)
    before = await projector.get_node(node_run_id=node_run_id)
    if before is None:
        raise HTTPException(status_code=404, detail="execution_node_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=before["chat_id"], action=Action.CANCEL,
    )
    try:
        await ExecutionPlanRepo(session).request_node_cancel(
            node_run_id=node_run_id,
            actor_id=auth.user_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    projection = await projector.get_node(node_run_id=node_run_id)
    return ExecutionNodeRunOut.model_validate(projection)


@router.get("/execution-plan-runs/{plan_run_id}/events")
async def stream_execution_plan_run_events(
    plan_run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    initial = await ExecutionPlanProjectionService(
        session, user_id=auth.user_id,
    ).get_run(plan_run_id=plan_run_id)
    if initial is None:
        raise HTTPException(status_code=404, detail="execution_plan_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=initial["chat_id"], action=Action.VIEW,
    )
    try:
        cursor = max(after, int(request.headers.get("last-event-id") or 0))
    except ValueError:
        cursor = after
    chat_id = initial["chat_id"]
    openfga_client = getattr(request.app.state, "openfga_client", None)
    await session.commit()

    async def event_stream():
        nonlocal cursor
        idle_ticks = 0
        next_auth_check = 0.0
        while not await request.is_disconnected():
            now = asyncio.get_running_loop().time()
            if now >= next_auth_check:
                allowed = await authorization_lease_is_valid(
                    auth=auth,
                    openfga_client=openfga_client,
                    resource=ResourceRef(
                        ResourceType.CHAT, chat_id, auth.active_organization_id,
                    ),
                    action=Action.VIEW,
                )
                if not allowed:
                    return
                next_auth_check = now + 5.0
            async with session_scope(tenant_id=auth.tenant_id) as event_session:
                events = await ExecutionPlanProjectionService(
                    event_session, user_id=auth.user_id,
                ).list_events(plan_run_id=plan_run_id, after=cursor)
            if events is None:
                return
            if events:
                idle_ticks = 0
                for event in events:
                    cursor = int(event["seq"])
                    yield format_event("execution_plan", event, event_id=cursor)
                continue
            idle_ticks += 1
            if idle_ticks >= 15:
                idle_ticks = 0
                yield b": heartbeat\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS,
    )


@router.get("/execution-plan-runs/{plan_run_id}/events/snapshot")
async def get_execution_plan_run_events_snapshot(
    plan_run_id: str,
    request: Request,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(tenant_db),
    auth: AuthContext = Depends(current_user),
    service: AuthzService = Depends(get_authz_service),
):
    projector = ExecutionPlanProjectionService(session, user_id=auth.user_id)
    run = await projector.get_run(plan_run_id=plan_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="execution_plan_run_not_found")
    await _authorize_chat(
        request=request, auth=auth, service=service,
        chat_id=run["chat_id"], action=Action.VIEW,
    )
    events = await projector.list_events(
        plan_run_id=plan_run_id, after=after, limit=limit,
    )
    return {"items": events or [], "last_event_seq": run["last_event_seq"]}
