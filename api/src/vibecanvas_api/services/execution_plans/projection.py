"""Authorized, decrypted product projections for Dynamic Execution Plans.

The Runtime never supplies these views.  Every preview is rebuilt from the
durable plan control plane so reloads, reconnects and multi-tab use share one
source of truth.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.storage.models_agent_runs import HitlRequest
from vibecanvas_api.storage.models_execution_plans import (
    ExecutionNodeAttempt,
    ExecutionNodeOutput,
    ExecutionNodeRun,
    ExecutionPlan,
    ExecutionPlanRevision,
    ExecutionPlanRun,
    ExecutionPlanRunEvent,
)
from vibecanvas_api.storage.hitl_repo import HitlRepo


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


class ExecutionPlanProjectionService:
    def __init__(self, session: AsyncSession, *, user_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.crypto = content_encryption_service()

    async def _decrypt(
        self,
        *,
        row: Any,
        chat_id: str,
        purpose: str,
        record_id: str,
        ciphertext_attr: str = "private_ciphertext",
        nonce_attr: str = "private_nonce",
        key_attr: str = "private_key_id",
    ) -> dict[str, Any]:
        value = await self.crypto.decrypt_json(
            self.session,
            key_id=getattr(row, key_attr),
            tenant_id=row.tenant_id,
            resource_type="chat",
            resource_id=chat_id,
            purpose=purpose,
            record_id=record_id,
            ciphertext=getattr(row, ciphertext_attr),
            nonce=getattr(row, nonce_attr),
        )
        if not isinstance(value, dict):
            raise ValueError(f"{purpose} must contain an object")
        return value

    async def _owned_run(self, plan_run_id: str) -> ExecutionPlanRun | None:
        return (
            await self.session.execute(
                select(ExecutionPlanRun).where(
                    ExecutionPlanRun.plan_run_id == plan_run_id,
                    ExecutionPlanRun.creator_user_id == self.user_id,
                )
            )
        ).scalar_one_or_none()

    async def _owned_plan(self, plan_id: str) -> ExecutionPlan | None:
        return (
            await self.session.execute(
                select(ExecutionPlan).where(
                    ExecutionPlan.plan_id == plan_id,
                    ExecutionPlan.creator_user_id == self.user_id,
                )
            )
        ).scalar_one_or_none()

    async def list_plans(self, *, chat_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = list((
            await self.session.execute(
                select(ExecutionPlan, ExecutionPlanRun, ExecutionPlanRevision)
                .join(
                    ExecutionPlanRun,
                    ExecutionPlanRun.plan_id == ExecutionPlan.plan_id,
                )
                .join(
                    ExecutionPlanRevision,
                    (ExecutionPlanRevision.plan_id == ExecutionPlanRun.plan_id)
                    & (ExecutionPlanRevision.revision == ExecutionPlanRun.revision),
                )
                .where(
                    ExecutionPlan.chat_id == chat_id,
                    ExecutionPlan.creator_user_id == self.user_id,
                )
                .order_by(ExecutionPlanRun.created_at.desc(), ExecutionPlanRun.plan_run_id.desc())
                .limit(limit)
            )
        ).all())
        result: list[dict[str, Any]] = []
        for plan, run, revision in rows:
            private = await self._decrypt(
                row=revision,
                chat_id=plan.chat_id,
                purpose="execution_plan_revision",
                record_id=f"{plan.plan_id}:{revision.revision}",
            )
            definition = private.get("definition") or {}
            result.append({
                "plan_id": plan.plan_id,
                "plan_run_id": run.plan_run_id,
                "job_id": run.background_job_id,
                "chat_id": plan.chat_id,
                "revision": revision.revision,
                "title": str(definition.get("title") or "Execution plan"),
                "status": run.status,
                "node_count": revision.node_count,
                "parallel_branch_count": revision.parallel_branch_count,
                "progress": dict(run.progress_summary_json or {}),
                "last_event_seq": int(run.last_event_seq or 0),
                "created_at": _iso(run.created_at),
                "updated_at": _iso(run.updated_at),
                "preview_resource": {
                    "schemaVersion": 1,
                    "kind": "execution_plan",
                    "planId": plan.plan_id,
                    "runId": run.plan_run_id,
                },
            })
        return result

    async def get_plan(self, *, plan_id: str, revision: int | None = None) -> dict[str, Any] | None:
        plan = await self._owned_plan(plan_id)
        if plan is None:
            return None
        revision_number = revision or plan.current_revision
        rev = await self.session.get(ExecutionPlanRevision, (plan_id, revision_number))
        if rev is None:
            return None
        private = await self._decrypt(
            row=rev,
            chat_id=plan.chat_id,
            purpose="execution_plan_revision",
            record_id=f"{plan_id}:{revision_number}",
        )
        runs = list((
            await self.session.execute(
                select(ExecutionPlanRun)
                .where(
                    ExecutionPlanRun.plan_id == plan_id,
                    ExecutionPlanRun.revision == revision_number,
                    ExecutionPlanRun.creator_user_id == self.user_id,
                )
                .order_by(ExecutionPlanRun.created_at.desc())
            )
        ).scalars().all())
        return {
            "plan_id": plan.plan_id,
            "chat_id": plan.chat_id,
            "revision": revision_number,
            "lifecycle_status": plan.lifecycle_status,
            "definition": private.get("definition") or {},
            "validation": private.get("validation") or {},
            "source_plan_path": rev.source_plan_path,
            "definition_hash": rev.definition_hash,
            "created_at": _iso(rev.created_at),
            "runs": [self._run_summary(row) for row in runs],
        }

    @staticmethod
    def _run_summary(run: ExecutionPlanRun) -> dict[str, Any]:
        return {
            "plan_run_id": run.plan_run_id,
            "job_id": run.background_job_id,
            "plan_id": run.plan_id,
            "revision": run.revision,
            "chat_id": run.chat_id,
            "status": run.status,
            "approval_mode": run.approval_mode_snapshot,
            "budget": dict(run.budget_json or {}),
            "progress": dict(run.progress_summary_json or {}),
            "last_event_seq": int(run.last_event_seq or 0),
            "cancel_requested": run.cancel_requested_at is not None,
            "started_at": _iso(run.started_at),
            "ended_at": _iso(run.ended_at),
            "created_at": _iso(run.created_at),
            "updated_at": _iso(run.updated_at),
        }

    async def get_run(self, *, plan_run_id: str) -> dict[str, Any] | None:
        run = await self._owned_run(plan_run_id)
        if run is None:
            return None
        node_rows = list((
            await self.session.execute(
                select(ExecutionNodeRun)
                .where(ExecutionNodeRun.plan_run_id == plan_run_id)
                .order_by(ExecutionNodeRun.node_path.asc())
            )
        ).scalars().all())
        nodes = [await self._node_summary(run, node) for node in node_rows]
        hitl = (
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.execution_plan_run_id == plan_run_id)
                .order_by(HitlRequest.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return {
            **self._run_summary(run),
            "nodes": nodes,
            "approval": None if hitl is None else {
                "hitl_request_id": hitl.hitl_request_id,
                "status": hitl.status,
                "created_at": _iso(hitl.created_at),
                "resolved_at": _iso(hitl.resolved_at),
            },
        }

    async def _node_summary(self, run: ExecutionPlanRun, node: ExecutionNodeRun) -> dict[str, Any]:
        private = await self._decrypt(
            row=node,
            chat_id=run.chat_id,
            purpose="execution_node_run",
            record_id=node.node_run_id,
        )
        approval_row = (
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.execution_node_run_id == node.node_run_id)
                .order_by(HitlRequest.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        approval = (
            await HitlRepo(self.session).get_request(approval_row.hitl_request_id)
            if approval_row is not None else None
        )
        return {
            "node_run_id": node.node_run_id,
            "node_path": node.node_path,
            "node_type": node.node_type,
            "status": node.status,
            "attention_status": node.attention_status,
            "current_attempt": node.current_attempt,
            "current_activity": str(private.get("current_activity") or ""),
            "definition": private.get("definition") or {},
            "result": private.get("result"),
            "output_ref": private.get("output_ref"),
            "error": private.get("error_detail") or {},
            "side_effect_state": node.side_effect_state,
            "progress": {"current": node.progress_current, "total": node.progress_total},
            "cancel_requested": node.cancel_requested_at is not None,
            "started_at": _iso(node.started_at),
            "ended_at": _iso(node.ended_at),
            "updated_at": _iso(node.updated_at),
            "approval": None if approval is None else {
                "hitl_request_id": approval.hitl_request_id,
                "status": approval.status,
                "title": approval.title,
                "prompt_text": approval.prompt_text,
                "tool_name": approval.ui_payload_json.get("tool_name"),
            },
        }

    async def get_node(self, *, node_run_id: str) -> dict[str, Any] | None:
        pair = (
            await self.session.execute(
                select(ExecutionNodeRun, ExecutionPlanRun)
                .join(ExecutionPlanRun, ExecutionPlanRun.plan_run_id == ExecutionNodeRun.plan_run_id)
                .where(
                    ExecutionNodeRun.node_run_id == node_run_id,
                    ExecutionPlanRun.creator_user_id == self.user_id,
                )
            )
        ).one_or_none()
        if pair is None:
            return None
        node, run = pair
        attempts = list((
            await self.session.execute(
                select(ExecutionNodeAttempt)
                .where(ExecutionNodeAttempt.node_run_id == node_run_id)
                .order_by(ExecutionNodeAttempt.attempt.desc())
            )
        ).scalars().all())
        chunks = list((
            await self.session.execute(
                select(ExecutionNodeOutput)
                .where(ExecutionNodeOutput.node_run_id == node_run_id)
                .order_by(ExecutionNodeOutput.seq.asc())
                .limit(500)
            )
        ).scalars().all())
        output: list[dict[str, Any]] = []
        for chunk in chunks:
            payload = await self._decrypt(
                row=chunk,
                chat_id=run.chat_id,
                purpose="execution_node_output",
                record_id=f"{node_run_id}:{chunk.seq}",
                ciphertext_attr="payload_ciphertext",
                nonce_attr="payload_nonce",
                key_attr="payload_key_id",
            )
            output.append({
                "seq": int(chunk.seq),
                "kind": chunk.output_kind,
                "content_type": chunk.content_type,
                "payload": payload,
                "created_at": _iso(chunk.created_at),
            })
        summary = await self._node_summary(run, node)
        summary["plan_run_id"] = run.plan_run_id
        summary["chat_id"] = run.chat_id
        summary["attempts"] = [{
            "attempt": row.attempt,
            "status": row.status,
            "heartbeat_at": _iso(row.heartbeat_at),
            "started_at": _iso(row.started_at),
            "ended_at": _iso(row.ended_at),
            "usage": dict(row.usage_json or {}),
        } for row in attempts]
        summary["output"] = output
        return summary

    async def list_node_output(
        self,
        *,
        node_run_id: str,
        after: int = 0,
        limit: int = 200,
    ) -> dict[str, Any] | None:
        pair = (
            await self.session.execute(
                select(ExecutionNodeRun, ExecutionPlanRun)
                .join(
                    ExecutionPlanRun,
                    ExecutionPlanRun.plan_run_id == ExecutionNodeRun.plan_run_id,
                )
                .where(
                    ExecutionNodeRun.node_run_id == node_run_id,
                    ExecutionPlanRun.creator_user_id == self.user_id,
                )
            )
        ).one_or_none()
        if pair is None:
            return None
        _, run = pair
        rows = list((await self.session.execute(
            select(ExecutionNodeOutput)
            .where(
                ExecutionNodeOutput.node_run_id == node_run_id,
                ExecutionNodeOutput.seq > after,
            )
            .order_by(ExecutionNodeOutput.seq.asc())
            .limit(max(1, min(limit, 500)))
        )).scalars().all())
        items: list[dict[str, Any]] = []
        for row in rows:
            payload = await self._decrypt(
                row=row,
                chat_id=run.chat_id,
                purpose="execution_node_output",
                record_id=f"{node_run_id}:{row.seq}",
                ciphertext_attr="payload_ciphertext",
                nonce_attr="payload_nonce",
                key_attr="payload_key_id",
            )
            items.append({
                "seq": int(row.seq),
                "kind": row.output_kind,
                "content_type": row.content_type,
                "payload": payload,
                "created_at": _iso(row.created_at),
            })
        return {
            "chat_id": run.chat_id,
            "items": items,
            "last_output_seq": int(items[-1]["seq"] if items else after),
            "has_more": len(items) >= max(1, min(limit, 500)),
        }

    async def list_events(self, *, plan_run_id: str, after: int, limit: int = 200) -> list[dict[str, Any]] | None:
        run = await self._owned_run(plan_run_id)
        if run is None:
            return None
        rows = list((
            await self.session.execute(
                select(ExecutionPlanRunEvent)
                .where(
                    ExecutionPlanRunEvent.plan_run_id == plan_run_id,
                    ExecutionPlanRunEvent.seq > after,
                )
                .order_by(ExecutionPlanRunEvent.seq.asc())
                .limit(limit)
            )
        ).scalars().all())
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = await self._decrypt(
                row=row,
                chat_id=run.chat_id,
                purpose="execution_plan_run_event",
                record_id=f"{plan_run_id}:{row.seq}",
                ciphertext_attr="payload_ciphertext",
                nonce_attr="payload_nonce",
                key_attr="payload_key_id",
            )
            events.append({
                "seq": int(row.seq),
                "event_type": row.event_type,
                "node_run_id": row.node_run_id,
                "attempt": row.attempt,
                "payload": payload,
                "trace_ref": row.trace_ref,
                "created_at": _iso(row.created_at),
            })
        return events
