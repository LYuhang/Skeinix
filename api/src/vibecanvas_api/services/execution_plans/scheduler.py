"""Durable scheduler for immutable Dynamic Execution Plan revisions.

PostgreSQL snapshots/events are authoritative.  Local asyncio tasks only own
live executor streams; losing them converges to ``executor_lost`` and never
replays a possibly side-effecting subagent automatically.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from sqlalchemy import func, select

from vibecanvas_api.config import config
from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.services.agent_runtime.model_capability import (
    authorization_model_generation,
    model_config_revision,
)
from vibecanvas_api.services.agent_runtime.orchestrator import private_runtime_root
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeBackgroundJobRequest,
    RuntimeType,
)
from vibecanvas_api.services.agent_runtime.workflow_model_capability import (
    mint_runtime_workflow_model_capability,
)
from vibecanvas_api.services.chat_workspace import chat_workspace_scope_id
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.services.sandbox.manager import get_sandbox_manager
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.models_execution_plans import (
    ExecutionNodeAttempt,
    ExecutionNodeOutput,
    ExecutionNodeRun,
    ExecutionPlanControl,
    ExecutionPlanRevision,
    ExecutionPlanRun,
    ExecutionPlanRunEvent,
)
from vibecanvas_api.storage.models_agent_runs import HitlRequest
from vibecanvas_api.storage.hitl_repo import HitlRepo
from vibecanvas_api.storage.vfs_store import VfsRepo


TERMINAL_NODE = {"succeeded", "failed", "cancelled", "skipped"}
TERMINAL_RUN = {"completed", "failed", "cancelled", "not_started"}
PLAN_INTERNAL_MAX_PARALLELISM = 4


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlanNodeExecutor(Protocol):
    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]: ...

    async def send_control(self, request: dict[str, Any]) -> bool: ...


class SandboxPlanNodeExecutor:
    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        sandbox = await get_sandbox_manager().get_session(
            request["tenant_id"],
            chat_workspace_scope_id(request["chat_id"]),
            user_id=request["user_id"],
            expose_run=False,
            expose_runtime=False,
            lease="resident",
        )
        async for item in sandbox.run_background_job_stream(request["runtime_request"]):
            yield item

    async def send_control(self, request: dict[str, Any]) -> bool:
        sandbox = await get_sandbox_manager().get_session(
            request["tenant_id"],
            chat_workspace_scope_id(request["chat_id"]),
            user_id=request["user_id"],
            expose_run=False,
            expose_runtime=False,
            lease="resident",
        )
        return await sandbox.send_background_job_control(
            request["job_id"], request["response"],
        )


class ExecutionPlanScheduler:
    def __init__(self, executor: PlanNodeExecutor | None = None) -> None:
        self.executor = executor or SandboxPlanNodeExecutor()
        self.owner = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()

    async def _decrypt(self, session, row, *, chat_id: str, purpose: str, record_id: str) -> dict[str, Any]:
        value = await content_encryption_service().decrypt_json(
            session,
            key_id=row.private_key_id,
            tenant_id=row.tenant_id,
            resource_type="chat",
            resource_id=chat_id,
            purpose=purpose,
            record_id=record_id,
            ciphertext=row.private_ciphertext,
            nonce=row.private_nonce,
        )
        return value if isinstance(value, dict) else {}

    async def _store_node_private(self, session, run: ExecutionPlanRun, node: ExecutionNodeRun, value: dict[str, Any]) -> None:
        encrypted = await content_encryption_service().encrypt_json(
            session,
            tenant_id=run.tenant_id,
            resource_type="chat",
            resource_id=run.chat_id,
            purpose="execution_node_run",
            record_id=node.node_run_id,
            value=value,
        )
        node.private_ciphertext = encrypted.ciphertext
        node.private_nonce = encrypted.nonce
        node.private_key_id = encrypted.key_id

    async def _event(self, session, run: ExecutionPlanRun, event_type: str, payload: dict[str, Any], node: ExecutionNodeRun | None = None, attempt: int | None = None) -> None:
        run.last_event_seq += 1
        run.updated_at = _now()
        encrypted = await content_encryption_service().encrypt_json(
            session,
            tenant_id=run.tenant_id,
            resource_type="chat",
            resource_id=run.chat_id,
            purpose="execution_plan_run_event",
            record_id=f"{run.plan_run_id}:{run.last_event_seq}",
            value=payload,
        )
        session.add(ExecutionPlanRunEvent(
            plan_run_id=run.plan_run_id,
            seq=run.last_event_seq,
            tenant_id=run.tenant_id,
            node_run_id=node.node_run_id if node else None,
            attempt=attempt,
            event_type=event_type,
            payload_ciphertext=encrypted.ciphertext,
            payload_nonce=encrypted.nonce,
            payload_key_id=encrypted.key_id,
        ))

    async def _definition(self, session, run: ExecutionPlanRun) -> dict[str, Any]:
        revision = await session.get(ExecutionPlanRevision, (run.plan_id, run.revision))
        if revision is None:
            raise RuntimeError("execution plan revision missing")
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
        return dict(private.get("definition") or {})

    async def _nodes(self, session, run: ExecutionPlanRun):
        rows = list((await session.execute(
            select(ExecutionNodeRun).where(ExecutionNodeRun.plan_run_id == run.plan_run_id).with_for_update()
        )).scalars().all())
        private = {
            row.node_path: await self._decrypt(
                session, row, chat_id=run.chat_id,
                purpose="execution_node_run", record_id=row.node_run_id,
            ) for row in rows
        }
        return rows, private

    async def _succeed_control(self, session, run, node, private, result: Any = None) -> None:
        node.status = "succeeded"
        node.started_at = node.started_at or _now()
        node.ended_at = _now()
        node.updated_at = _now()
        private["current_activity"] = "Completed"
        private["result"] = result
        await self._store_node_private(session, run, node, private)
        await self._event(session, run, "node_succeeded", {"status": "succeeded", "node_path": node.node_path}, node)

    @staticmethod
    def _predecessors(
        definitions: dict[str, dict[str, Any]],
    ) -> dict[str, list[str]]:
        result = {node_id: [] for node_id in definitions}
        for source, item in definitions.items():
            for target in item.get("next") or []:
                if target in result:
                    result[target].append(source)
        return result

    @classmethod
    def _reconcile_graph(
        cls,
        *,
        definitions: dict[str, dict[str, Any]],
        by_path: dict[str, ExecutionNodeRun],
    ) -> int:
        """Project static DAG dependencies into ready/skipped node states.

        Fan-out is represented by multiple outgoing edges. Fan-in is an
        ordinary node with multiple predecessors and becomes ready only after
        every predecessor succeeds. A terminal failed/cancelled/skipped
        predecessor makes the dependent node impossible and propagates a skip.
        """
        predecessors = cls._predecessors(definitions)
        changed = 0
        for _ in range(len(by_path) + 1):
            pass_changed = False
            for node_id, row in by_path.items():
                if row.status != "pending" or row.node_type == "start":
                    continue
                parents = predecessors.get(node_id) or []
                parent_states = [by_path[parent].status for parent in parents]
                if any(state in {"failed", "cancelled", "skipped"} for state in parent_states):
                    row.status = "skipped"
                    row.error_code = "upstream_not_succeeded"
                    row.ended_at = _now()
                    row.updated_at = _now()
                    pass_changed = True
                    changed += 1
                elif parents and all(state == "succeeded" for state in parent_states):
                    row.status = "ready"
                    row.updated_at = _now()
                    pass_changed = True
                    changed += 1
            if not pass_changed:
                break
        return changed

    @staticmethod
    async def _write_result_manifest(
        session,
        *,
        run: ExecutionPlanRun,
        rows: list[ExecutionNodeRun],
        private: dict[str, dict[str, Any]],
    ) -> str:
        """Persist the complete node-result projection as one Chat VFS file."""
        path = f"/data/plans/runs/{run.plan_run_id}/results.json"
        payload = {
            "schema_version": 1,
            "plan_id": run.plan_id,
            "plan_run_id": run.plan_run_id,
            "status": run.status,
            "nodes": [
                {
                    "id": row.node_path,
                    "type": row.node_type,
                    "title": str(
                        (private.get(row.node_path, {}).get("definition") or {}).get("title")
                        or row.node_path
                    ),
                    "status": row.status,
                    "result": private.get(row.node_path, {}).get("result"),
                    "output_ref": private.get(row.node_path, {}).get("output_ref"),
                    "error": private.get(row.node_path, {}).get("error_detail") or {},
                }
                for row in sorted(rows, key=lambda item: item.node_path)
            ],
        }
        await VfsRepo(session, object_store=get_object_store()).upsert_artifact(
            wf_id=chat_workspace_scope_id(run.chat_id),
            tenant=str(run.tenant_id),
            path=path,
            content=json.dumps(payload, ensure_ascii=False, indent=2),
            content_type="application/json",
            abstract="Execution plan node results",
        )
        return path

    async def _deliver_node_approvals(
        self,
        session,
        run: ExecutionPlanRun,
        rows: list[ExecutionNodeRun],
    ) -> int:
        sender = getattr(self.executor, "send_control", None)
        if sender is None or not rows:
            return 0
        node_by_id = {row.node_run_id: row for row in rows}
        requests = list((await session.execute(
            select(HitlRequest).where(
                HitlRequest.execution_node_run_id.in_(list(node_by_id)),
                HitlRequest.hitl_type == "plan_node_tool_approval",
                HitlRequest.status.in_(("approved", "denied", "cancelled", "expired")),
            ).order_by(HitlRequest.created_at.asc()).with_for_update(skip_locked=True)
        )).scalars().all())
        delivered = 0
        repo = HitlRepo(session)
        for raw in requests:
            request = await repo.get_request(raw.hitl_request_id)
            if request is None or bool((request.resume_payload_json or {}).get("control_delivered")):
                continue
            node = node_by_id.get(str(request.execution_node_run_id))
            if node is None:
                continue
            action = "approve" if request.status == "approved" else (
                "cancel" if request.status == "cancelled" else "deny"
            )
            sent = await sender({
                "tenant_id": str(run.tenant_id),
                "user_id": str(run.creator_user_id),
                "chat_id": run.chat_id,
                "job_id": node.node_run_id,
                "response": {
                    "action": action,
                    "persisted": True,
                    "hitl_request_id": request.hitl_request_id,
                    "correlation": dict(request.runtime_correlation_json or {}),
                },
            })
            if not sent:
                continue
            await repo.mark_runtime_control_delivered(request.hitl_request_id)
            node.attention_status = "none"
            node.updated_at = _now()
            await self._event(
                session, run, "node_attention_changed",
                {"node_path": node.node_path, "attention": "none", "hitl_request_id": request.hitl_request_id, "decision": request.status},
                node, node.current_attempt or None,
            )
            delivered += 1
        return delivered

    async def tick_tenant(self, tenant_id: str) -> int:
        progressed = 0
        async with session_scope(tenant_id=tenant_id) as session:
            runs = list((await session.execute(
                select(ExecutionPlanRun).where(
                    ExecutionPlanRun.status.in_(("queued", "running", "cancel_requested"))
                ).order_by(ExecutionPlanRun.created_at.asc()).with_for_update(skip_locked=True)
            )).scalars().all())
            for run in runs:
                definition = await self._definition(session, run)
                rows, private = await self._nodes(session, run)
                by_path = {row.node_path: row for row in rows}
                definitions = {item["id"]: item for item in definition.get("nodes") or []}
                progressed += await self._deliver_node_approvals(
                    session, run, rows,
                )

                if run.status == "queued":
                    run.status = "running"
                    run.started_at = run.started_at or _now()
                    await self._event(session, run, "run_started", {"status": "running"})
                    progressed += 1

                wall_limit = int((run.budget_json or {}).get("max_wall_time_seconds") or 1800)
                wall_expired = bool(
                    run.started_at and (_now() - run.started_at).total_seconds() >= wall_limit
                )
                if wall_expired and run.status == "running":
                    run.status = "cancel_requested"
                    run.cancel_requested_at = run.cancel_requested_at or _now()

                # Node-scoped cancellation never stops an independent sibling.
                # Cancelling the local consumer closes the executor stream; its
                # adapter finally-block terminates the matching sandbox process.
                for row in rows:
                    if row.status != "cancel_requested":
                        continue
                    task = self._tasks.get(row.node_run_id)
                    if task is not None and not task.done():
                        task.cancel()

                if run.status == "cancel_requested":
                    cancel_reason = "wall_time_exceeded" if wall_expired else "user_requested"
                    for row in rows:
                        task = self._tasks.get(row.node_run_id)
                        if task and not task.done():
                            task.cancel()
                        if row.status not in TERMINAL_NODE:
                            if row.status in {"running", "cancel_requested"}:
                                row.side_effect_state = "unknown"
                            row.status = "cancelled" if row.status in {"ready", "queued", "running", "cancel_requested"} else "skipped"
                            row.cancel_requested_at = row.cancel_requested_at or _now()
                            row.ended_at = _now()
                            row.updated_at = _now()
                    run.status = "cancelled"
                    run.ended_at = _now()
                    controls = list((await session.execute(
                        select(ExecutionPlanControl).where(
                            ExecutionPlanControl.plan_run_id == run.plan_run_id,
                            ExecutionPlanControl.action == "cancel_run",
                            ExecutionPlanControl.delivery_status == "pending",
                        ).with_for_update()
                    )).scalars().all())
                    for control in controls:
                        control.delivery_status = "applied"
                        control.applied_at = _now()
                    await self._event(session, run, "run_cancel_signal_delivered", {"status": "cancel_requested", "forced": False})
                    result_path = await self._write_result_manifest(
                        session, run=run, rows=rows, private=private,
                    )
                    run.progress_summary_json = {
                        **dict(run.progress_summary_json or {}),
                        "result_ref": result_path,
                        "result_summary": f"Execution plan cancelled: {cancel_reason}.",
                    }
                    await self._event(session, run, "run_cancelled", {
                        "status": "cancelled",
                        "reason": cancel_reason,
                        "result_ref": result_path,
                    })
                    progressed += 1
                    continue

                # Expired leases without a live local task are never replayed.
                expired = list((await session.execute(
                    select(ExecutionNodeAttempt).where(
                        ExecutionNodeAttempt.node_run_id.in_([row.node_run_id for row in rows]),
                        ExecutionNodeAttempt.status == "running",
                        ExecutionNodeAttempt.lease_expires_at < _now(),
                    ).with_for_update(skip_locked=True)
                )).scalars().all())
                for attempt in expired:
                    if attempt.node_run_id in self._tasks and not self._tasks[attempt.node_run_id].done():
                        continue
                    node = next(row for row in rows if row.node_run_id == attempt.node_run_id)
                    if node.status == "cancel_requested":
                        attempt.status = "cancelled"
                        attempt.ended_at = _now()
                        node.status = "cancelled"
                        node.side_effect_state = "unknown"
                        node.ended_at = _now()
                        node.updated_at = _now()
                        controls = list((await session.execute(
                            select(ExecutionPlanControl).where(
                                ExecutionPlanControl.node_run_id == node.node_run_id,
                                ExecutionPlanControl.action == "cancel_node",
                                ExecutionPlanControl.delivery_status == "pending",
                            ).with_for_update()
                        )).scalars().all())
                        for control in controls:
                            control.delivery_status = "applied"
                            control.applied_at = _now()
                        await self._event(
                            session, run, "node_cancelled",
                            {"status": "cancelled", "code": "executor_disconnected_after_cancel", "node_path": node.node_path},
                            node, attempt.attempt,
                        )
                        progressed += 1
                        continue
                    attempt.status = "failed"
                    attempt.ended_at = _now()
                    node.status = "failed"
                    node.error_code = "executor_lost"
                    node.side_effect_state = "unknown"
                    node.ended_at = _now()
                    node.updated_at = _now()
                    p = private[node.node_path]
                    p["error_detail"] = {"code": "executor_lost", "message": "Execution stopped because its worker or sandbox is no longer available."}
                    await self._store_node_private(session, run, node, p)
                    await self._event(session, run, "node_failed", {"status": "failed", "code": "executor_lost", "node_path": node.node_path}, node, attempt.attempt)
                    progressed += 1

                progressed += self._reconcile_graph(
                    definitions=definitions, by_path=by_path,
                )
                # Start/end are zero-cost control nodes. Reconcile after each
                # pass so a chain of already-satisfied joins settles in one tick.
                for _ in range(len(rows) + 1):
                    changed = False
                    for node in rows:
                        if node.status != "ready" or node.node_type == "subagent":
                            continue
                        if node.node_type == "start":
                            await self._succeed_control(session, run, node, private[node.node_path], {})
                        elif node.node_type == "end":
                            final_outputs = {
                                path: value.get("result")
                                for path, value in private.items()
                                if path != node.node_path
                                and value.get("result") is not None
                            }
                            await self._succeed_control(
                                session, run, node, private[node.node_path],
                                {"status": "completed", "outputs": final_outputs},
                            )
                        changed = True
                        progressed += 1
                    progressed += self._reconcile_graph(
                        definitions=definitions, by_path=by_path,
                    )
                    if not changed:
                        break

                running_count = sum(row.status in {"queued", "running"} for row in rows)
                parallel_limit = PLAN_INTERNAL_MAX_PARALLELISM
                for node in rows:
                    if node.node_type != "subagent" or node.status != "ready" or running_count >= parallel_limit:
                        continue
                    node.status = "queued"
                    node.updated_at = _now()
                    await self._event(session, run, "node_queued", {"status": "queued", "node_path": node.node_path}, node)
                    self._launch(str(run.tenant_id), run.plan_run_id, node.node_run_id)
                    running_count += 1
                    progressed += 1

                top_level = rows
                if top_level and all(row.status in TERMINAL_NODE for row in top_level):
                    if all(row.status == "succeeded" for row in top_level):
                        run.status = "completed"
                        event_type = "run_completed"
                    elif any(row.status == "failed" for row in top_level):
                        run.status = "failed"
                        event_type = "run_failed"
                    else:
                        run.status = "cancelled"
                        event_type = "run_cancelled"
                    run.ended_at = _now()
                    result_path = await self._write_result_manifest(
                        session, run=run, rows=top_level, private=private,
                    )
                    run.progress_summary_json = {
                        **dict(run.progress_summary_json or {}),
                        "result_ref": result_path,
                        "result_summary": (
                            "Execution plan completed."
                            if run.status == "completed"
                            else f"Execution plan ended with status {run.status}."
                        ),
                    }
                    await self._event(
                        session, run, event_type,
                        {"status": run.status, "result_ref": result_path},
                    )
                    progressed += 1
                run.progress_summary_json = {
                    **dict(run.progress_summary_json or {}),
                    "completed_nodes": sum(row.status in TERMINAL_NODE for row in top_level),
                    "total_nodes": len(top_level),
                    "running_nodes": sum(row.status == "running" for row in rows),
                }
        return progressed

    def _launch(self, tenant_id: str, plan_run_id: str, node_run_id: str) -> None:
        current = self._tasks.get(node_run_id)
        if current is not None and not current.done():
            return
        task = asyncio.create_task(self._execute_node(tenant_id, plan_run_id, node_run_id), name=f"plan-node:{node_run_id}")
        self._tasks[node_run_id] = task
        task.add_done_callback(lambda done, key=node_run_id: self._tasks.pop(key, None) if self._tasks.get(key) is done else None)

    async def _execute_node(self, tenant_id: str, plan_run_id: str, node_run_id: str) -> None:
        output_seq = 0
        # One plan node is one background task. The platform never silently
        # creates a second attempt; a user/agent-requested rerun is a new task.
        for attempt_number in (1,):
            streamed_trace_count = 0
            async with session_scope(tenant_id=tenant_id) as session:
                run = await session.get(ExecutionPlanRun, plan_run_id, with_for_update=True)
                node = await session.get(ExecutionNodeRun, node_run_id, with_for_update=True)
                if run is None or node is None or node.status in TERMINAL_NODE:
                    return
                definition_private = await self._decrypt(session, node, chat_id=run.chat_id, purpose="execution_node_run", record_id=node.node_run_id)
                definition = dict(definition_private.get("definition") or {})
                node.status = "running"
                node.current_attempt = attempt_number
                node.started_at = node.started_at or _now()
                node.updated_at = _now()
                definition_private["current_activity"] = "Starting subagent"
                await self._store_node_private(session, run, node, definition_private)
                attempt_id = f"{node_run_id}:{attempt_number}"
                attempt_private = await content_encryption_service().encrypt_json(
                    session, tenant_id=run.tenant_id, resource_type="chat", resource_id=run.chat_id,
                    purpose="execution_node_attempt", record_id=attempt_id,
                    value={"execution_handle": node_run_id},
                )
                attempt = ExecutionNodeAttempt(
                    node_run_id=node_run_id, attempt=attempt_number, tenant_id=run.tenant_id,
                    status="running", idempotency_key=f"{plan_run_id}:{node.node_path}:{attempt_number}",
                    private_ciphertext=attempt_private.ciphertext, private_nonce=attempt_private.nonce,
                    private_key_id=attempt_private.key_id, lease_owner=self.owner, lease_generation=1,
                    lease_expires_at=_now() + timedelta(seconds=45), started_at=_now(), heartbeat_at=_now(),
                )
                session.add(attempt)
                output_seq = int((await session.execute(
                    select(func.max(ExecutionNodeOutput.seq)).where(
                        ExecutionNodeOutput.node_run_id == node_run_id,
                    )
                )).scalar_one_or_none() or 0)
                await self._event(session, run, "node_started", {"status": "running", "node_path": node.node_path, "attempt": attempt_number}, node, attempt_number)
                runtime_request = self._runtime_request(run, node, definition)

            final: dict[str, Any] | None = None
            try:
                async for item in self.executor.stream({
                    "tenant_id": tenant_id, "user_id": str(run.creator_user_id),
                    "chat_id": run.chat_id, "runtime_request": runtime_request,
                }):
                    async with session_scope(tenant_id=tenant_id) as session:
                        live_run = await session.get(ExecutionPlanRun, plan_run_id, with_for_update=True)
                        live_node = await session.get(ExecutionNodeRun, node_run_id, with_for_update=True)
                        live_attempt = await session.get(ExecutionNodeAttempt, (node_run_id, attempt_number), with_for_update=True)
                        if live_run is None or live_node is None or live_attempt is None:
                            return
                        if live_node.status == "cancel_requested" or live_run.status == "cancel_requested":
                            raise asyncio.CancelledError
                        live_attempt.heartbeat_at = _now()
                        live_attempt.lease_expires_at = _now() + timedelta(seconds=45)
                        if item.get("kind") == "event":
                            approval = item.get("approval") if isinstance(item.get("approval"), dict) else None
                            if approval is not None:
                                hitl_request_id = str(approval.get("hitl_request_id") or "")
                                if hitl_request_id:
                                    await HitlRepo(session).create_request(
                                        hitl_request_id=hitl_request_id,
                                        tenant_id=live_run.tenant_id,
                                        chat_id=live_run.chat_id,
                                        run_id=None,
                                        execution_node_run_id=live_node.node_run_id,
                                        artifact_id=None,
                                        hitl_type="plan_node_tool_approval",
                                        title=str(approval.get("title") or "Approve Plan tool"),
                                        prompt_text=str(approval.get("prompt_text") or "Review this operation."),
                                        ui_payload_json={
                                            "plan_run_id": live_run.plan_run_id,
                                            "node_run_id": live_node.node_run_id,
                                            "node_path": live_node.node_path,
                                            "tool_name": approval.get("tool_name"),
                                        },
                                        agent_payload_json={
                                            "tool": approval.get("tool_name"),
                                            "arguments": approval.get("arguments") if isinstance(approval.get("arguments"), dict) else {},
                                        },
                                        runtime_correlation_json=approval.get("runtime_correlation") if isinstance(approval.get("runtime_correlation"), dict) else {},
                                        mark_run_waiting=False,
                                    )
                                    live_node.attention_status = "waiting_tool_approval"
                                    private_value = await self._decrypt(session, live_node, chat_id=live_run.chat_id, purpose="execution_node_run", record_id=live_node.node_run_id)
                                    private_value["current_activity"] = f"Waiting for approval: {approval.get('tool_name') or 'tool'}"
                                    await self._store_node_private(session, live_run, live_node, private_value)
                                    await self._event(
                                        session, live_run, "node_attention_changed",
                                        {"node_path": live_node.node_path, "attention": "waiting_tool_approval", "hitl_request_id": hitl_request_id},
                                        live_node, attempt_number,
                                    )
                                continue
                            progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
                            live_node.current_attempt = attempt_number
                            live_node.progress_current = int(progress.get("current") or 0)
                            live_node.progress_total = progress.get("total")
                            private_value = await self._decrypt(session, live_node, chat_id=live_run.chat_id, purpose="execution_node_run", record_id=live_node.node_run_id)
                            trace_entry = item.get("trace_entry") if isinstance(item.get("trace_entry"), dict) else None
                            if trace_entry is not None:
                                output_seq += 1
                                streamed_trace_count += 1
                                output_kind = "tool_activity" if trace_entry.get("tool_calls") else "text"
                                encrypted = await content_encryption_service().encrypt_json(
                                    session, tenant_id=live_run.tenant_id,
                                    resource_type="chat", resource_id=live_run.chat_id,
                                    purpose="execution_node_output",
                                    record_id=f"{node_run_id}:{output_seq}",
                                    value=trace_entry,
                                )
                                session.add(ExecutionNodeOutput(
                                    node_run_id=node_run_id, seq=output_seq,
                                    tenant_id=live_run.tenant_id,
                                    output_kind=output_kind,
                                    content_type="application/vnd.skeinix.agent-activity+json",
                                    payload_ciphertext=encrypted.ciphertext,
                                    payload_nonce=encrypted.nonce,
                                    payload_key_id=encrypted.key_id,
                                ))
                                private_value["current_activity"] = (
                                    "Using a tool" if output_kind == "tool_activity"
                                    else "Writing response"
                                )
                                await self._store_node_private(session, live_run, live_node, private_value)
                                await self._event(
                                    session, live_run,
                                    "node_tool_activity" if output_kind == "tool_activity" else "node_output_delta",
                                    {"node_path": live_node.node_path, "output_seq": output_seq, "output_kind": output_kind},
                                    live_node, attempt_number,
                                )
                                continue
                            private_value["current_activity"] = str(progress.get("message") or "Working")
                            await self._store_node_private(session, live_run, live_node, private_value)
                            await self._event(session, live_run, "node_progress", {"node_path": live_node.node_path, "progress": progress}, live_node, attempt_number)
                        elif item.get("kind") == "result":
                            final = item
                if final is None:
                    raise RuntimeError("executor stream ended without a result")
            except asyncio.CancelledError:
                async with session_scope(tenant_id=tenant_id) as session:
                    run = await session.get(ExecutionPlanRun, plan_run_id, with_for_update=True)
                    node = await session.get(ExecutionNodeRun, node_run_id, with_for_update=True)
                    attempt = await session.get(ExecutionNodeAttempt, (node_run_id, attempt_number), with_for_update=True)
                    if run and node and attempt:
                        attempt.status = "cancelled"; attempt.ended_at = _now()
                        node.status = "cancelled"; node.side_effect_state = "unknown"; node.ended_at = _now(); node.updated_at = _now()
                        controls = list((await session.execute(
                            select(ExecutionPlanControl).where(
                                ExecutionPlanControl.node_run_id == node.node_run_id,
                                ExecutionPlanControl.action == "cancel_node",
                                ExecutionPlanControl.delivery_status == "pending",
                            ).with_for_update()
                        )).scalars().all())
                        for control in controls:
                            control.delivery_status = "applied"
                            control.applied_at = _now()
                        await self._event(session, run, "node_cancel_signal_delivered", {"status": "cancel_requested", "node_path": node.node_path, "forced": False}, node, attempt_number)
                        await self._event(session, run, "node_cancelled", {"status": "cancelled", "node_path": node.node_path}, node, attempt_number)
                return
            except Exception as exc:
                final = {"status": "error", "error": {"code": "executor_failed", "message": str(exc)}, "result": {}}

            result = final.get("result") if isinstance(final.get("result"), dict) else {}
            executor_status = str(final.get("status") or "error")
            if executor_status == "done":
                async with session_scope(tenant_id=tenant_id) as session:
                    run = await session.get(ExecutionPlanRun, plan_run_id, with_for_update=True)
                    node = await session.get(ExecutionNodeRun, node_run_id, with_for_update=True)
                    attempt = await session.get(ExecutionNodeAttempt, (node_run_id, attempt_number), with_for_update=True)
                    if run is None or node is None or attempt is None: return
                    attempt.status = "succeeded"; attempt.ended_at = _now(); attempt.output_ref = f"execution-node:{node_run_id}:result"
                    node.status = "succeeded"; node.ended_at = _now(); node.updated_at = _now()
                    private_value = await self._decrypt(session, node, chat_id=run.chat_id, purpose="execution_node_run", record_id=node.node_run_id)
                    private_value.update({"result": result, "current_activity": "Completed", "output_ref": attempt.output_ref, "error_detail": {}})
                    await self._store_node_private(session, run, node, private_value)
                    trace = final.get("trace") if isinstance(final.get("trace"), list) else []
                    for entry in trace[streamed_trace_count:][-200:]:
                        output_seq += 1
                        encrypted = await content_encryption_service().encrypt_json(
                            session, tenant_id=run.tenant_id, resource_type="chat", resource_id=run.chat_id,
                            purpose="execution_node_output", record_id=f"{node_run_id}:{output_seq}",
                            value=entry if isinstance(entry, dict) else {"text": str(entry)},
                        )
                        session.add(ExecutionNodeOutput(
                            node_run_id=node_run_id, seq=output_seq, tenant_id=run.tenant_id,
                            output_kind="tool_activity" if isinstance(entry, dict) and entry.get("tool_calls") else "text",
                            content_type="application/vnd.skeinix.agent-activity+json",
                            payload_ciphertext=encrypted.ciphertext, payload_nonce=encrypted.nonce, payload_key_id=encrypted.key_id,
                        ))
                    output_seq += 1
                    result_payload = {
                        "result": result,
                        "result_ref": attempt.output_ref,
                        "artifact_refs": [],
                    }
                    result_encrypted = await content_encryption_service().encrypt_json(
                        session, tenant_id=run.tenant_id,
                        resource_type="chat", resource_id=run.chat_id,
                        purpose="execution_node_output",
                        record_id=f"{node_run_id}:{output_seq}",
                        value=result_payload,
                    )
                    session.add(ExecutionNodeOutput(
                        node_run_id=node_run_id, seq=output_seq,
                        tenant_id=run.tenant_id, output_kind="result",
                        content_type="application/json",
                        payload_ciphertext=result_encrypted.ciphertext,
                        payload_nonce=result_encrypted.nonce,
                        payload_key_id=result_encrypted.key_id,
                    ))
                    await self._event(
                        session, run, "node_result_committed",
                        {
                            "status": "succeeded",
                            "node_path": node.node_path,
                            "result_ref": attempt.output_ref,
                            "output_seq": output_seq,
                            "artifact_refs": [],
                        },
                        node, attempt_number,
                    )
                    await self._event(session, run, "node_succeeded", {"status": "succeeded", "node_path": node.node_path, "result_ref": attempt.output_ref}, node, attempt_number)
                return
            error = final.get("error") if isinstance(final.get("error"), dict) else {}
            error_code = str(error.get("code") or "subagent_incomplete")
            error_message = str(error.get("message") or "The subagent did not complete its task.")
            async with session_scope(tenant_id=tenant_id) as session:
                run = await session.get(ExecutionPlanRun, plan_run_id, with_for_update=True)
                node = await session.get(ExecutionNodeRun, node_run_id, with_for_update=True)
                attempt = await session.get(ExecutionNodeAttempt, (node_run_id, attempt_number), with_for_update=True)
                if run and node and attempt:
                    attempt.status = "failed"; attempt.ended_at = _now()
                    node.status = "failed"; node.error_code = error_code; node.ended_at = _now(); node.updated_at = _now()
                    private_value = await self._decrypt(session, node, chat_id=run.chat_id, purpose="execution_node_run", record_id=node.node_run_id)
                    private_value["error_detail"] = {"code": error_code, "message": error_message}
                    await self._store_node_private(session, run, node, private_value)
                    await self._event(session, run, "node_failed", {"status": "failed", "code": error_code, "node_path": node.node_path}, node, attempt_number)

    @staticmethod
    def _runtime_request(run, node, definition) -> dict[str, Any]:
        model_value = str(config.agent.model or "")
        provider, separator, model = model_value.partition(":")
        provider = provider.strip().lower().replace("-", "_") if separator else ""
        model = model.strip() if separator else model_value.strip()
        capability = mint_runtime_workflow_model_capability(
            organization_id=str(run.tenant_id), user_id=str(run.creator_user_id),
            workflow_id=run.chat_id, execution_id=run.plan_run_id,
            execution_resource_type="agent_plan", credential_id=None,
            provider=provider, model=model,
            config_revision=model_config_revision(provider=provider, model=model, updated_at="platform-process-config"),
            authorization_generation=authorization_model_generation(model_id=config.openfga_authorization_model_id),
            secret=config.signing_secret,
            ttl_s=max(int((run.budget_json or {}).get("max_wall_time_seconds") or 1800) + 300, 1200),
        )
        model_config = config.agent.to_agent_cfg()
        model_config = {key: value for key, value in model_config.items() if key not in {"api_key", "base_url", "proxy"}}
        model_config.update({
            "id": model,
            "base_url": f"{config.mcp.platform_internal_base_url}/api/internal/runtime-model/v1",
            "api_key": capability,
        })
        prompt = str(definition.get("task") or "")
        request = RuntimeBackgroundJobRequest(
            tenant_id=str(run.tenant_id), user_id=str(run.creator_user_id), chat_id=run.chat_id,
            parent_turn_id=run.create_turn_id, job_id=node.node_run_id,
            runtime_root=private_runtime_root(RuntimeType.LANGCHAIN, run.chat_id),
            title=str(definition.get("title") or node.node_path)[:160], prompt=prompt,
            max_iterations=25, model=model_config,
            system_prompt=(
                "You are one bounded worker in an approved static execution plan. "
                "Work only on the self-contained delegated task. Treat every "
                "absolute VFS path in the task as a fixed handoff contract: read "
                "declared upstream files and write declared result files exactly "
                "as instructed. Expose public progress through tool activity, then "
                "finish by calling set_output exactly once with a concise result or "
                "the primary result file path."
            ),
            output_fields=None,
            approval_mode=run.approval_mode_snapshot,
            approval_owner="execution_plan",
        )
        return request.model_dump(mode="json")

    async def run_loop(self, interval_seconds: float = 1.0) -> None:
        from vibecanvas_api.storage.models import Tenant
        if self._stop.is_set():
            self._stop = asyncio.Event()
        while not self._stop.is_set():
            try:
                async with session_scope() as session:
                    tenant_ids = [str(value) for value in (await session.execute(select(Tenant.tenant_id))).scalars().all()]
                for tenant_id in tenant_ids:
                    await self.tick_tenant(tenant_id)
            except Exception:
                import structlog
                structlog.get_logger(__name__).exception("execution_plan_scheduler_tick_failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval_seconds)
            except asyncio.TimeoutError:
                pass

    async def shutdown(self) -> None:
        self._stop.set()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


execution_plan_scheduler = ExecutionPlanScheduler()
