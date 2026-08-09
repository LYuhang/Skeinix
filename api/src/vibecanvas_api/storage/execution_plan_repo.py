"""Transactional repository for immutable Execution Plan submissions."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import content_encryption_service
from vibecanvas_api.services.execution_plans.validator import PlanValidationReport
from .models_agent_runs import HitlRequest

from .models_execution_plans import (
    ExecutionNodeRun,
    ExecutionPlan,
    ExecutionPlanControl,
    ExecutionPlanControlDelivery,
    ExecutionPlanEvent,
    ExecutionPlanRevision,
    ExecutionPlanRun,
    ExecutionPlanRunEvent,
)


ACTIVE_PLAN_RUN_STATUSES = (
    "awaiting_approval",
    "queued",
    "running",
    "cancel_requested",
)


@dataclass(frozen=True, slots=True)
class ExecutionPlanSubmission:
    plan_id: str
    revision: int
    plan_run_id: str
    job_id: str
    status: str
    created: bool


def _opaque(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class ExecutionPlanRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _encrypt(
        self,
        *,
        tenant_id: str | uuid.UUID,
        chat_id: str,
        purpose: str,
        record_id: str,
        value: Any,
    ):
        return await content_encryption_service().encrypt_json(
            self.session,
            tenant_id=tenant_id,
            resource_type="chat",
            resource_id=chat_id,
            purpose=purpose,
            record_id=record_id,
            value=value,
        )

    async def _existing_invocation(
        self,
        *,
        chat_id: str,
        creator_user_id: str,
        tool_invocation_id: str,
    ) -> ExecutionPlanRun | None:
        return (
            await self.session.execute(
                select(ExecutionPlanRun).where(
                    ExecutionPlanRun.chat_id == chat_id,
                    ExecutionPlanRun.creator_user_id == _uuid(creator_user_id),
                    ExecutionPlanRun.create_tool_invocation_id == tool_invocation_id,
                )
            )
        ).scalar_one_or_none()

    async def create_validated(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        creator_user_id: str,
        parent_turn_id: str,
        tool_invocation_id: str,
        approval_mode: str,
        authorization_snapshot_hash: str,
        report: PlanValidationReport,
    ) -> ExecutionPlanSubmission:
        """Persist one valid source snapshot and its idempotent Run request."""
        if report.status != "valid" or report.definition is None or report.errors:
            raise ValueError("only a fully validated plan can be persisted")
        existing = await self._existing_invocation(
            chat_id=chat_id,
            creator_user_id=creator_user_id,
            tool_invocation_id=tool_invocation_id,
        )
        if existing is not None:
            return ExecutionPlanSubmission(
                plan_id=existing.plan_id,
                revision=existing.revision,
                plan_run_id=existing.plan_run_id,
                job_id=existing.background_job_id,
                status=existing.status,
                created=False,
            )

        current = (
            await self.session.execute(
                select(ExecutionPlan, ExecutionPlanRevision)
                .join(
                    ExecutionPlanRevision,
                    (ExecutionPlanRevision.plan_id == ExecutionPlan.plan_id)
                    & (ExecutionPlanRevision.revision == ExecutionPlan.current_revision),
                )
                .where(
                    ExecutionPlan.chat_id == chat_id,
                    ExecutionPlan.creator_user_id == _uuid(creator_user_id),
                    ExecutionPlanRevision.source_plan_path == report.plan_path,
                )
                .with_for_update()
            )
        ).first()
        if current is None:
            plan = ExecutionPlan(
                plan_id=_opaque("plan"),
                tenant_id=_uuid(tenant_id),
                chat_id=chat_id,
                creator_user_id=_uuid(creator_user_id),
                current_revision=1,
                lifecycle_status="draft",
                last_plan_event_seq=0,
            )
            revision_number = 1
            self.session.add(plan)
        else:
            plan = current[0]
            active = (
                await self.session.execute(
                    select(ExecutionPlanRun.plan_run_id).where(
                        ExecutionPlanRun.plan_id == plan.plan_id,
                        ExecutionPlanRun.status.in_(ACTIVE_PLAN_RUN_STATUSES),
                    )
                )
            ).scalar_one_or_none()
            if active is not None:
                raise ValueError(
                    "active_plan_run_exists: cancel the current background job "
                    "before submitting a new revision"
                )
            revision_number = plan.current_revision + 1
            plan.current_revision = revision_number
            plan.lifecycle_status = "draft"

        definition = report.definition.model_dump(mode="json", by_alias=True)
        canonical = json.dumps(
            definition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        definition_hash = "sha256:" + hashlib.sha256(canonical).hexdigest()
        revision_id = f"{plan.plan_id}:{revision_number}"
        revision_private = await self._encrypt(
            tenant_id=tenant_id,
            chat_id=chat_id,
            purpose="execution_plan_revision",
            record_id=revision_id,
            value={
                "definition": definition,
                "validation": report.model_dump(
                    mode="json", exclude={"definition"}
                ),
            },
        )
        # Keep the existing projection column as a count of explicit fan-out
        # edges. Parallelism is topology now; there is no hidden branch model.
        parallel_branches = sum(
            max(0, len(getattr(node, "next", [])) - 1)
            for node in report.definition.nodes
        )
        revision = ExecutionPlanRevision(
            plan_id=plan.plan_id,
            revision=revision_number,
            tenant_id=_uuid(tenant_id),
            schema_version=1,
            definition_hash=definition_hash,
            private_ciphertext=revision_private.ciphertext,
            private_nonce=revision_private.nonce,
            private_key_id=revision_private.key_id,
            source_plan_path=report.plan_path,
            source_content_hash=report.source_hash or definition_hash,
            source_size_bytes=report.source_size_bytes,
            validation_status="valid",
            node_count=len(report.definition.nodes),
            parallel_branch_count=parallel_branches,
            planner_runtime_type="langchain",
            parent_turn_id=parent_turn_id,
            created_by=_uuid(creator_user_id),
        )
        self.session.add(revision)

        plan_run_id = _opaque("planrun")
        job_id = _opaque("job_plan")
        run_status = (
            "queued" if approval_mode == "always_allow" else "awaiting_approval"
        )
        if run_status == "queued":
            plan.lifecycle_status = "approved"
        run = ExecutionPlanRun(
            plan_run_id=plan_run_id,
            background_job_id=job_id,
            tenant_id=_uuid(tenant_id),
            plan_id=plan.plan_id,
            revision=revision_number,
            chat_id=chat_id,
            creator_user_id=_uuid(creator_user_id),
            status=run_status,
            create_turn_id=parent_turn_id,
            create_tool_invocation_id=tool_invocation_id,
            approval_mode_snapshot=approval_mode,
            executor_policy="langchain_detached_v1",
            authorization_snapshot_hash=authorization_snapshot_hash,
            budget_json=report.definition.budgets.model_dump(mode="json"),
            progress_summary_json={
                "completed_nodes": 0,
                "total_nodes": len(report.definition.nodes),
            },
        )
        self.session.add(run)

        hitl_request_id = None
        if run_status == "awaiting_approval":
            hitl_request_id = _opaque("hitl_plan")
            run.approval_control_id = hitl_request_id

        # Materialize the FK spine before adding Node/HITL rows. SQLAlchemy's
        # unit-of-work cannot infer every insert edge here because these domain
        # models intentionally avoid cross-domain ORM relationships.
        await self.session.flush()

        control_id = _opaque("planctl")
        control_private = await self._encrypt(
            tenant_id=tenant_id,
            chat_id=chat_id,
            purpose="execution_plan_control",
            record_id=control_id,
            value={"source_plan_path": report.plan_path},
        )
        self.session.add(ExecutionPlanControl(
            control_id=control_id,
            tenant_id=_uuid(tenant_id),
            plan_id=plan.plan_id,
            plan_run_id=plan_run_id,
            action="create",
            actor_type="agent",
            actor_id=creator_user_id,
            expected_revision=revision_number,
            idempotency_key=f"create:{tool_invocation_id}",
            tool_invocation_id=tool_invocation_id,
            delivery_status="applied",
            private_ciphertext=control_private.ciphertext,
            private_nonce=control_private.nonce,
            private_key_id=control_private.key_id,
        ))

        for node in report.definition.nodes:
            node_run_id = _opaque("plannode")
            node_private = await self._encrypt(
                    tenant_id=tenant_id,
                    chat_id=chat_id,
                    purpose="execution_node_run",
                    record_id=node_run_id,
                    value={
                        "definition": node.model_dump(mode="json", by_alias=True),
                        "current_activity": "",
                        "output_ref": None,
                        "error_detail": {},
                    },
                )
            self.session.add(ExecutionNodeRun(
                node_run_id=node_run_id,
                tenant_id=_uuid(tenant_id),
                plan_run_id=plan_run_id,
                node_path=node.id,
                node_type=node.type,
                status=(
                    "ready"
                    if run_status == "queued" and node.type == "start"
                    else "pending"
                ),
                private_ciphertext=node_private.ciphertext,
                private_nonce=node_private.nonce,
                private_key_id=node_private.key_id,
            ))
        if hitl_request_id is not None:
            # Reuse the platform's durable approval surface without suspending
            # the planner Runtime or mutating the foreground AgentRun.
            from vibecanvas_api.storage.hitl_repo import HitlRepo

            await HitlRepo(self.session).create_request(
                hitl_request_id=hitl_request_id,
                tenant_id=tenant_id,
                chat_id=chat_id,
                run_id=None,
                execution_plan_run_id=plan_run_id,
                artifact_id=None,
                hitl_type="plan_start_approval",
                title=f"Start plan: {report.definition.title}",
                prompt_text=(
                    "Review the immutable execution plan, then approve it to "
                    "start the background run."
                ),
                ui_payload_json={
                    "plan_id": plan.plan_id,
                    "revision": revision_number,
                    "plan_run_id": plan_run_id,
                    "job_id": job_id,
                    "preview_resource": f"execution_plan:{plan.plan_id}:{revision_number}",
                },
                agent_payload_json={"job_id": job_id},
                runtime_correlation_json={
                    "kind": "execution_plan_start",
                    "plan_run_id": plan_run_id,
                    "revision": revision_number,
                },
                mark_run_waiting=False,
            )

        plan.last_plan_event_seq += 1
        plan_event_private = await self._encrypt(
            tenant_id=tenant_id,
            chat_id=chat_id,
            purpose="execution_plan_event",
            record_id=f"{plan.plan_id}:{plan.last_plan_event_seq}",
            value={"revision": revision_number, "status": "submitted"},
        )
        self.session.add(ExecutionPlanEvent(
            plan_id=plan.plan_id,
            seq=plan.last_plan_event_seq,
            tenant_id=_uuid(tenant_id),
            plan_run_id=plan_run_id,
            event_type="revision_submitted",
            payload_ciphertext=plan_event_private.ciphertext,
            payload_nonce=plan_event_private.nonce,
            payload_key_id=plan_event_private.key_id,
        ))
        run.last_event_seq = 1
        run_event_private = await self._encrypt(
            tenant_id=tenant_id,
            chat_id=chat_id,
            purpose="execution_plan_run_event",
            record_id=f"{plan_run_id}:1",
            value={"status": run_status},
        )
        self.session.add(ExecutionPlanRunEvent(
            plan_run_id=plan_run_id,
            seq=1,
            tenant_id=_uuid(tenant_id),
            event_type="run_created",
            payload_ciphertext=run_event_private.ciphertext,
            payload_nonce=run_event_private.nonce,
            payload_key_id=run_event_private.key_id,
        ))
        await self.session.flush()
        return ExecutionPlanSubmission(
            plan_id=plan.plan_id,
            revision=revision_number,
            plan_run_id=plan_run_id,
            job_id=job_id,
            status=run_status,
            created=True,
        )

    async def request_cancel(
        self,
        *,
        plan_run_id: str,
        actor_id: str,
        reason: str,
        actor_type: str = "agent",
        idempotency_key: str | None = None,
    ) -> ExecutionPlanRun:
        """Persist a queued/running Plan cancel without assuming an executor."""
        probe = await self.session.get(ExecutionPlanRun, plan_run_id)
        if probe is None:
            raise LookupError("execution plan run not found")
        plan = (
            await self.session.execute(
                select(ExecutionPlan)
                .where(ExecutionPlan.plan_id == probe.plan_id)
                .with_for_update()
            )
        ).scalar_one()
        run = (
            await self.session.execute(
                select(ExecutionPlanRun)
                .where(ExecutionPlanRun.plan_run_id == plan_run_id)
                .with_for_update()
            )
        ).scalar_one()
        if idempotency_key:
            existing_control = (
                await self.session.execute(
                    select(ExecutionPlanControl.control_id).where(
                        ExecutionPlanControl.plan_id == run.plan_id,
                        ExecutionPlanControl.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if existing_control is not None:
                return run
        if run.status in {"completed", "failed", "cancelled", "not_started"}:
            return run
        if run.status == "awaiting_approval":
            raise ValueError("awaiting approval must be cancelled through its HITL")
        now = datetime.now(timezone.utc)
        run.cancel_requested_at = run.cancel_requested_at or now
        if run.status == "queued":
            run.status = "cancelled"
            run.ended_at = now
            event_type = "run_cancelled"
        else:
            run.status = "cancel_requested"
            event_type = "run_cancel_requested"
        run.updated_at = now

        control_id = _opaque("planctl")
        control_private = await self._encrypt(
            tenant_id=run.tenant_id,
            chat_id=run.chat_id,
            purpose="execution_plan_control",
            record_id=control_id,
            value={"reason": reason},
        )
        self.session.add(ExecutionPlanControl(
            control_id=control_id,
            tenant_id=run.tenant_id,
            plan_id=plan.plan_id,
            plan_run_id=run.plan_run_id,
            action="cancel_run",
            actor_type=actor_type,
            actor_id=actor_id,
            reason_code=reason,
            expected_revision=run.revision,
            idempotency_key=idempotency_key or f"cancel:{run.plan_run_id}:{run.last_event_seq + 1}",
            delivery_status="applied" if run.status == "cancelled" else "pending",
            private_ciphertext=control_private.ciphertext,
            private_nonce=control_private.nonce,
            private_key_id=control_private.key_id,
            applied_at=now if run.status == "cancelled" else None,
        ))
        if run.status == "cancelled":
            nodes = list((
                await self.session.execute(
                    select(ExecutionNodeRun)
                    .where(
                        ExecutionNodeRun.plan_run_id == run.plan_run_id,
                        ExecutionNodeRun.status.in_(("pending", "ready", "queued")),
                    )
                    .with_for_update()
                )
            ).scalars().all())
            for node in nodes:
                node.status = "cancelled"
                node.cancel_requested_at = now
                node.ended_at = now
                node.updated_at = now

        # A graph-level cancellation owns every node-level approval. Resolve
        # those durable waiters even for a running Run so refresh/restart never
        # leaves an actionable approval card attached to cancelled work.
        pending_approvals = list((await self.session.execute(
            select(HitlRequest.hitl_request_id)
            .join(
                ExecutionNodeRun,
                ExecutionNodeRun.node_run_id == HitlRequest.execution_node_run_id,
            )
            .where(
                ExecutionNodeRun.plan_run_id == run.plan_run_id,
                HitlRequest.status == "pending",
                HitlRequest.hitl_type == "plan_node_tool_approval",
            )
        )).scalars().all())
        if pending_approvals:
            from .hitl_repo import HitlRepo
            hitl_repo = HitlRepo(self.session)
            for request_id in pending_approvals:
                await hitl_repo.resolve(
                    hitl_request_id=request_id,
                    decision="cancel",
                    decision_payload={"reason": reason},
                    interaction_result={"reason": reason},
                    actor_id=actor_id,
                )

        run.last_event_seq += 1
        event_private = await self._encrypt(
            tenant_id=run.tenant_id,
            chat_id=run.chat_id,
            purpose="execution_plan_run_event",
            record_id=f"{run.plan_run_id}:{run.last_event_seq}",
            value={"status": run.status, "reason": reason},
        )
        self.session.add(ExecutionPlanRunEvent(
            plan_run_id=run.plan_run_id,
            seq=run.last_event_seq,
            tenant_id=run.tenant_id,
            event_type=event_type,
            payload_ciphertext=event_private.ciphertext,
            payload_nonce=event_private.nonce,
            payload_key_id=event_private.key_id,
        ))
        await self.session.flush()
        return run

    async def request_node_cancel(
        self,
        *,
        node_run_id: str,
        actor_id: str,
        reason: str,
        idempotency_key: str,
        actor_type: str = "user",
    ) -> ExecutionNodeRun:
        """Persist an idempotent Node cancel and a durable agent projection."""
        probe = await self.session.get(ExecutionNodeRun, node_run_id)
        if probe is None:
            raise LookupError("execution node run not found")
        run = (
            await self.session.execute(
                select(ExecutionPlanRun)
                .where(ExecutionPlanRun.plan_run_id == probe.plan_run_id)
                .with_for_update()
            )
        ).scalar_one()
        node = (
            await self.session.execute(
                select(ExecutionNodeRun)
                .where(ExecutionNodeRun.node_run_id == node_run_id)
                .with_for_update()
            )
        ).scalar_one()
        if node.node_type != "subagent":
            raise ValueError("only subagent nodes can be cancelled independently")
        existing = (
            await self.session.execute(
                select(ExecutionPlanControl).where(
                    ExecutionPlanControl.plan_id == run.plan_id,
                    ExecutionPlanControl.idempotency_key == idempotency_key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return node
        if node.status in {"succeeded", "failed", "cancelled", "skipped"}:
            return node

        now = datetime.now(timezone.utc)
        node.cancel_requested_at = node.cancel_requested_at or now
        if node.status in {"pending", "ready", "queued"}:
            node.status = "cancelled"
            node.ended_at = now
            event_type = "node_cancelled"
            delivery_status = "applied"
        else:
            node.status = "cancel_requested"
            node.side_effect_state = "possible"
            event_type = "node_cancel_requested"
            delivery_status = "pending"
        node.updated_at = now

        pending_approvals = list((await self.session.execute(
            select(HitlRequest.hitl_request_id).where(
                HitlRequest.execution_node_run_id == node.node_run_id,
                HitlRequest.status == "pending",
                HitlRequest.hitl_type == "plan_node_tool_approval",
            )
        )).scalars().all())
        if pending_approvals:
            from .hitl_repo import HitlRepo
            hitl_repo = HitlRepo(self.session)
            for request_id in pending_approvals:
                await hitl_repo.resolve(
                    hitl_request_id=request_id,
                    decision="cancel",
                    decision_payload={"reason": reason},
                    interaction_result={"reason": reason},
                    actor_id=actor_id,
                )

        control_id = _opaque("planctl")
        control_private = await self._encrypt(
            tenant_id=run.tenant_id,
            chat_id=run.chat_id,
            purpose="execution_plan_control",
            record_id=control_id,
            value={
                "reason": reason,
                "node_path": node.node_path,
                "final_status": node.status,
            },
        )
        self.session.add(ExecutionPlanControl(
            control_id=control_id,
            tenant_id=run.tenant_id,
            plan_id=run.plan_id,
            plan_run_id=run.plan_run_id,
            node_run_id=node.node_run_id,
            action="cancel_node",
            actor_type=actor_type,
            actor_id=actor_id,
            reason_code=reason,
            expected_revision=run.revision,
            idempotency_key=idempotency_key,
            delivery_status=delivery_status,
            private_ciphertext=control_private.ciphertext,
            private_nonce=control_private.nonce,
            private_key_id=control_private.key_id,
            applied_at=now if delivery_status == "applied" else None,
        ))
        run.last_event_seq += 1
        run.updated_at = now
        event_private = await self._encrypt(
            tenant_id=run.tenant_id,
            chat_id=run.chat_id,
            purpose="execution_plan_run_event",
            record_id=f"{run.plan_run_id}:{run.last_event_seq}",
            value={
                "status": node.status,
                "node_path": node.node_path,
                "reason": reason,
            },
        )
        self.session.add(ExecutionPlanRunEvent(
            plan_run_id=run.plan_run_id,
            seq=run.last_event_seq,
            tenant_id=run.tenant_id,
            node_run_id=node.node_run_id,
            event_type=event_type,
            payload_ciphertext=event_private.ciphertext,
            payload_nonce=event_private.nonce,
            payload_key_id=event_private.key_id,
        ))
        await self.session.flush()
        return node

    async def claim_control_projections(
        self,
        *,
        chat_id: str,
        creator_user_id: str,
        delivered_to_turn_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Claim undelivered Plan-control facts for exactly one future Turn.

        A pending cancellation is projection version 1; its terminal applied or
        failed state is version 2.  Locking the control rows and inserting the
        delivery ledger in the caller's Turn transaction makes retries safe and
        lets the main Agent observe both a request and its later outcome without
        replaying either fact indefinitely.
        """
        controls = list((
            await self.session.execute(
                select(ExecutionPlanControl)
                .join(
                    ExecutionPlanRun,
                    ExecutionPlanRun.plan_run_id
                    == ExecutionPlanControl.plan_run_id,
                )
                .where(
                    ExecutionPlanRun.chat_id == chat_id,
                    ExecutionPlanRun.creator_user_id == _uuid(creator_user_id),
                    ExecutionPlanControl.action.in_(("approve_start", "cancel_run", "cancel_node")),
                )
                .order_by(ExecutionPlanControl.requested_at.asc())
                .limit(max(1, min(limit, 100)))
                .with_for_update(of=ExecutionPlanControl, skip_locked=True)
            )
        ).scalars().all())
        projections: list[dict[str, Any]] = []
        for control in controls:
            projection_version = (
                2 if control.delivery_status in {"applied", "failed"} else 1
            )
            delivered = await self.session.get(
                ExecutionPlanControlDelivery,
                (control.control_id, projection_version),
            )
            if delivered is not None:
                continue
            run = await self.session.get(ExecutionPlanRun, control.plan_run_id)
            if run is None:  # pragma: no cover - protected by the FK
                continue
            node = (
                await self.session.get(ExecutionNodeRun, control.node_run_id)
                if control.node_run_id
                else None
            )
            private = await content_encryption_service().decrypt_json(
                self.session,
                key_id=control.private_key_id,
                tenant_id=control.tenant_id,
                resource_type="chat",
                resource_id=chat_id,
                purpose="execution_plan_control",
                record_id=control.control_id,
                ciphertext=control.private_ciphertext,
                nonce=control.private_nonce,
            )
            private = private if isinstance(private, dict) else {}
            projections.append({
                "control_id": control.control_id,
                "projection_version": projection_version,
                "action": control.action,
                "plan_id": control.plan_id,
                "plan_run_id": control.plan_run_id,
                "node_run_id": control.node_run_id,
                "node_path": node.node_path if node is not None else None,
                "status": node.status if node is not None else run.status,
                "reason": private.get("reason") or control.reason_code,
                "side_effect_state": (
                    node.side_effect_state if node is not None else None
                ),
            })
            self.session.add(ExecutionPlanControlDelivery(
                control_id=control.control_id,
                projection_version=projection_version,
                tenant_id=control.tenant_id,
                chat_id=chat_id,
                delivered_to_turn_id=delivered_to_turn_id,
            ))
            control.delivered_at = datetime.now(timezone.utc)
        await self.session.flush()
        return projections
