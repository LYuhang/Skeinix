"""Provider registry for LangChain-private background job controls.

Tool jobs and Dynamic Execution Plan runs keep independent physical schemas;
this module is the small projection/control boundary that lets one model tool
surface manage both without conflating their state machines.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.storage.background_jobs_repo import (
    BackgroundJobsRepo,
    _output_envelope,
    _sanitize_public_output,
    project_background_job,
)
from vibecanvas_api.storage.execution_plan_repo import ExecutionPlanRepo
from vibecanvas_api.storage.hitl_repo import HitlRepo
from vibecanvas_api.storage.models import Chat
from vibecanvas_api.storage.models_agent_runs import HitlRequest
from vibecanvas_api.storage.models_background_jobs import ACTIVE_BACKGROUND_JOB_STATUSES
from vibecanvas_api.storage.models_execution_plans import (
    ExecutionNodeRun,
    ExecutionPlanRevision,
    ExecutionPlanRun,
)


ACTIVE_PLAN_JOB_STATUSES = (
    "awaiting_approval",
    "queued",
    "running",
    "cancel_requested",
)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _project_plan_job(
    session: AsyncSession,
    run: ExecutionPlanRun,
) -> dict:
    revision = await session.get(
        ExecutionPlanRevision,
        (run.plan_id, run.revision),
    )
    title = "Execution plan"
    if revision is not None:
        private = await content_encryption_service().decrypt_json(
            session,
            key_id=revision.private_key_id,
            tenant_id=revision.tenant_id,
            resource_type="chat",
            resource_id=run.chat_id,
            purpose="execution_plan_revision",
            record_id=f"{run.plan_id}:{run.revision}",
            ciphertext=revision.private_ciphertext,
            nonce=revision.private_nonce,
        )
        if isinstance(private, dict):
            definition = private.get("definition")
            if isinstance(definition, dict) and definition.get("title"):
                title = str(definition["title"])[:200]
    progress = dict(run.progress_summary_json or {})
    completed = max(0, int(progress.get("completed_nodes") or 0))
    total = max(completed, int(progress.get("total_nodes") or 0))
    result: dict = {}
    result_ref = progress.get("result_ref")
    end_node = (
        await session.execute(
            select(ExecutionNodeRun)
            .where(
                ExecutionNodeRun.plan_run_id == run.plan_run_id,
                ExecutionNodeRun.node_type == "end",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if end_node is not None:
        node_private = await content_encryption_service().decrypt_json(
            session,
            key_id=end_node.private_key_id,
            tenant_id=end_node.tenant_id,
            resource_type="chat",
            resource_id=run.chat_id,
            purpose="execution_node_run",
            record_id=end_node.node_run_id,
            ciphertext=end_node.private_ciphertext,
            nonce=end_node.private_nonce,
        )
        if isinstance(node_private, dict):
            raw_result = node_private.get("result")
            result = _sanitize_public_output(
                raw_result if isinstance(raw_result, dict) else {},
            )
            result_ref = node_private.get("output_ref") or result_ref
    error = dict(progress.get("error") or {})
    output = _output_envelope(
        status=("completed" if run.status == "completed" else run.status),
        result=result,
        result_ref=result_ref,
        error=error,
    )
    return {
        "job_id": run.background_job_id,
        "provider": "execution_plan",
        "chat_id": run.chat_id,
        "parent_run_id": run.create_turn_id,
        "runtime_type": "langchain",
        "executor_type": "execution_plan",
        "tool_name": "create_execution_plan",
        "title": title,
        "status": run.status,
        "attention": (
            {"kind": "approval", "request_id": run.approval_control_id}
            if run.status == "awaiting_approval"
            else None
        ),
        "progress": {
            "current": completed,
            "total": total,
            "message": str(progress.get("current_activity") or ""),
        },
        "output": output,
        "result": result,
        "result_ref": result_ref,
        "error": error,
        "event_seq": run.last_event_seq,
        "cancel_requested": run.cancel_requested_at is not None,
        "delivery_status": "not_applicable",
        "plan": {
            "plan_id": run.plan_id,
            "revision": run.revision,
            "plan_run_id": run.plan_run_id,
            "preview_resource": f"execution_plan:{run.plan_id}:{run.revision}",
        },
        "created_at": _iso(run.created_at),
        "started_at": _iso(run.started_at),
        "finished_at": _iso(run.ended_at),
        "updated_at": _iso(run.updated_at),
    }


async def list_background_jobs(
    session: AsyncSession,
    *,
    chat_id: str,
    creator_user_id: str,
    include_finished: bool,
    limit: int,
) -> list[dict]:
    page = await list_background_jobs_page(
        session,
        chat_id=chat_id,
        creator_user_id=creator_user_id,
        include_finished=include_finished,
        limit=limit,
        cursor=None,
    )
    return page["jobs"]


def _cursor_encode(item: dict, active_statuses: set[str]) -> str:
    raw = json.dumps({
        "rank": 0 if item.get("status") in active_statuses else 1,
        "updated_at": str(item.get("updated_at") or ""),
        "job_id": str(item.get("job_id") or ""),
    }, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(cursor: str | None) -> tuple[int, str, str] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = json.loads(base64.urlsafe_b64decode(padded).decode())
        return int(value["rank"]), str(value["updated_at"]), str(value["job_id"])
    except Exception as exc:
        raise ValueError("invalid background job cursor") from exc


async def list_background_jobs_page(
    session: AsyncSession,
    *,
    chat_id: str,
    creator_user_id: str,
    include_finished: bool,
    limit: int,
    cursor: str | None,
) -> dict:
    bounded = max(1, min(int(limit), 100))
    fetch_limit = min(200, bounded * 2 + 1)
    tool_repo = BackgroundJobsRepo(session)
    await tool_repo.reconcile_stale_for_chat(chat_id=chat_id)
    tool_rows = await tool_repo.list_for_user(
        chat_id=chat_id,
        creator_user_id=creator_user_id,
        statuses=None if include_finished else ACTIVE_BACKGROUND_JOB_STATUSES,
        limit=fetch_limit,
    )
    plan_query = (
        select(ExecutionPlanRun)
        .join(Chat, Chat.chat_id == ExecutionPlanRun.chat_id)
        .where(
            ExecutionPlanRun.chat_id == chat_id,
            Chat.creator_user_id == creator_user_id,
            Chat.deleted_at.is_(None),
        )
    )
    if not include_finished:
        plan_query = plan_query.where(
            ExecutionPlanRun.status.in_(ACTIVE_PLAN_JOB_STATUSES)
        )
    plan_query = plan_query.order_by(
        case(
            (ExecutionPlanRun.status.in_(ACTIVE_PLAN_JOB_STATUSES), 0),
            else_=1,
        ),
        ExecutionPlanRun.updated_at.desc(),
    ).limit(fetch_limit)
    plan_rows = list((await session.execute(plan_query)).scalars().all())
    jobs = [project_background_job(row) for row in tool_rows]
    jobs.extend([await _project_plan_job(session, row) for row in plan_rows])
    active_statuses = {*ACTIVE_BACKGROUND_JOB_STATUSES, *ACTIVE_PLAN_JOB_STATUSES}
    jobs.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    jobs.sort(key=lambda item: 0 if item.get("status") in active_statuses else 1)
    decoded = _cursor_decode(cursor)
    if decoded is not None:
        cursor_rank, cursor_updated, cursor_id = decoded
        jobs = [
            item for item in jobs
            if (
                (0 if item.get("status") in active_statuses else 1) > cursor_rank
                or (
                    (0 if item.get("status") in active_statuses else 1) == cursor_rank
                    and (
                        str(item.get("updated_at") or "") < cursor_updated
                        or (
                            str(item.get("updated_at") or "") == cursor_updated
                            and str(item.get("job_id") or "") < cursor_id
                        )
                    )
                )
            )
        ]
    page = jobs[:bounded]
    return {
        "jobs": page,
        "next_cursor": (
            _cursor_encode(page[-1], active_statuses)
            if len(jobs) > bounded and page else None
        ),
    }


async def get_background_job(
    session: AsyncSession,
    *,
    chat_id: str,
    job_id: str,
    creator_user_id: str,
) -> dict | None:
    if job_id.startswith("job_plan_"):
        run = (
            await session.execute(
                select(ExecutionPlanRun)
                .join(Chat, Chat.chat_id == ExecutionPlanRun.chat_id)
                .where(
                    ExecutionPlanRun.background_job_id == job_id,
                    ExecutionPlanRun.chat_id == chat_id,
                    Chat.creator_user_id == creator_user_id,
                    Chat.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return await _project_plan_job(session, run) if run is not None else None
    repo = BackgroundJobsRepo(session)
    await repo.reconcile_stale_for_chat(chat_id=chat_id)
    row = await repo.get_for_user(
        chat_id=chat_id,
        job_id=job_id,
        creator_user_id=creator_user_id,
    )
    return project_background_job(row) if row is not None else None


async def cancel_background_job(
    session: AsyncSession,
    *,
    chat_id: str,
    job_id: str,
    creator_user_id: str,
) -> dict | None:
    if job_id.startswith("job_plan_"):
        run = (
            await session.execute(
                select(ExecutionPlanRun)
                .join(Chat, Chat.chat_id == ExecutionPlanRun.chat_id)
                .where(
                    ExecutionPlanRun.background_job_id == job_id,
                    ExecutionPlanRun.chat_id == chat_id,
                    Chat.creator_user_id == creator_user_id,
                    Chat.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if run is None:
            return None
        if run.status == "awaiting_approval":
            request = (
                await session.execute(
                    select(HitlRequest).where(
                        HitlRequest.execution_plan_run_id == run.plan_run_id,
                        HitlRequest.status == "pending",
                    )
                )
            ).scalar_one_or_none()
            if request is not None:
                await HitlRepo(session).resolve(
                    hitl_request_id=request.hitl_request_id,
                    decision="cancel",
                    decision_payload={"reason": "agent_requested"},
                )
        else:
            await ExecutionPlanRepo(session).request_cancel(
                plan_run_id=run.plan_run_id,
                actor_id=creator_user_id,
                reason="agent_requested",
            )
        refreshed = await session.get(ExecutionPlanRun, run.plan_run_id)
        return await _project_plan_job(session, refreshed) if refreshed else None
    row = await BackgroundJobsRepo(session).request_cancel(
        chat_id=chat_id,
        job_id=job_id,
        creator_user_id=creator_user_id,
        reason="agent_requested",
    )
    return project_background_job(row) if row is not None else None
