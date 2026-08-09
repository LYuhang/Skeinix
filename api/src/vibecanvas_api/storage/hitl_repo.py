"""Durable HITL and interactive-artifact state repository."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.security.content_encryption import content_encryption_service

from .models import Chat
from .models_agent_runs import (
    AgentRun,
    HitlRequest,
    InteractiveArtifact,
)
from .models_execution_plans import (
    ExecutionNodeRun,
    ExecutionPlan,
    ExecutionPlanControl,
    ExecutionPlanRun,
    ExecutionPlanRunEvent,
)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


class HitlRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def commit(self) -> None:
        await self.session.commit()

    async def _store_request_private(self, row: HitlRequest) -> None:
        encrypted = await content_encryption_service().encrypt_json(
            self.session,
            tenant_id=row.tenant_id,
            resource_type="chat",
            resource_id=row.chat_id,
            purpose="hitl_request_private",
            record_id=row.hitl_request_id,
            value={
                "title": row.title,
                "prompt_text": row.prompt_text,
                "ui_payload_json": row.ui_payload_json,
                "agent_payload_json": row.agent_payload_json,
                "decision_payload_json": row.decision_payload_json,
                "runtime_correlation_json": row.runtime_correlation_json,
                "resume_payload_json": row.resume_payload_json,
                "interaction_result_json": row.interaction_result_json,
            },
        )
        row.private_ciphertext = encrypted.ciphertext
        row.private_nonce = encrypted.nonce
        row.private_key_id = encrypted.key_id

    async def _materialize_request(
        self,
        row: HitlRequest | None,
    ) -> HitlRequest | None:
        if row is None:
            return None
        value = await content_encryption_service().decrypt_json(
            self.session,
            key_id=row.private_key_id,
            tenant_id=row.tenant_id,
            resource_type="chat",
            resource_id=row.chat_id,
            purpose="hitl_request_private",
            record_id=row.hitl_request_id,
            ciphertext=row.private_ciphertext,
            nonce=row.private_nonce,
        )
        if not isinstance(value, dict):
            raise ValueError("HITL ciphertext must contain an object")
        for name in (
            "title",
            "prompt_text",
            "ui_payload_json",
            "agent_payload_json",
            "decision_payload_json",
            "runtime_correlation_json",
            "resume_payload_json",
            "interaction_result_json",
        ):
            setattr(row, name, value.get(name))
        return row

    async def _store_artifact_private(self, row: InteractiveArtifact) -> None:
        encrypted = await content_encryption_service().encrypt_json(
            self.session,
            tenant_id=row.tenant_id,
            resource_type="chat",
            resource_id=row.chat_id,
            purpose="interactive_artifact_private",
            record_id=row.artifact_id,
            value={
                "title": row.title,
                "definition_json": row.definition_json,
                "widget_state_json": row.widget_state_json,
                "interaction_result_json": row.interaction_result_json,
                "artifact_ref": row.artifact_ref,
            },
        )
        row.private_ciphertext = encrypted.ciphertext
        row.private_nonce = encrypted.nonce
        row.private_key_id = encrypted.key_id

    async def _materialize_artifact(
        self,
        row: InteractiveArtifact | None,
    ) -> InteractiveArtifact | None:
        if row is None:
            return None
        value = await content_encryption_service().decrypt_json(
            self.session,
            key_id=row.private_key_id,
            tenant_id=row.tenant_id,
            resource_type="chat",
            resource_id=row.chat_id,
            purpose="interactive_artifact_private",
            record_id=row.artifact_id,
            ciphertext=row.private_ciphertext,
            nonce=row.private_nonce,
        )
        if not isinstance(value, dict):
            raise ValueError("Interactive Artifact ciphertext must contain an object")
        for name in (
            "title",
            "definition_json",
            "widget_state_json",
            "interaction_result_json",
            "artifact_ref",
        ):
            setattr(row, name, value.get(name))
        return row

    async def create_interactive_artifact(
        self,
        *,
        artifact_id: str,
        tenant_id: str | uuid.UUID,
        chat_id: str,
        run_id: str | None,
        component_type: str,
        completion_mode: str,
        title: str,
        definition_json: dict,
        artifact_ref: str | None,
        content_hash: str | None,
        hitl_request_id: str | None = None,
    ) -> InteractiveArtifact:
        existing = await self.session.get(InteractiveArtifact, artifact_id)
        if existing is not None:
            materialized = await self._materialize_artifact(existing)
            assert materialized is not None
            return materialized
        row = InteractiveArtifact(
            artifact_id=artifact_id,
            tenant_id=_uuid(tenant_id),
            chat_id=chat_id,
            run_id=run_id or None,
            hitl_request_id=hitl_request_id,
            component_type=component_type,
            completion_mode=completion_mode,
            content_hash=content_hash,
        )
        row.title = title
        row.definition_json = _safe_json(definition_json)
        row.widget_state_json = _safe_json(definition_json.get("widget_state"))
        row.interaction_result_json = {}
        row.artifact_ref = artifact_ref
        await self._store_artifact_private(row)
        self.session.add(row)
        await self.session.flush()
        return row

    async def create_request(
        self,
        *,
        hitl_request_id: str,
        tenant_id: str | uuid.UUID,
        chat_id: str,
        run_id: str | None,
        artifact_id: str | None,
        hitl_type: str,
        title: str,
        prompt_text: str,
        ui_payload_json: dict,
        agent_payload_json: dict,
        runtime_correlation_json: dict,
        execution_plan_run_id: str | None = None,
        execution_node_run_id: str | None = None,
        resume_payload_json: dict | None = None,
        mark_run_waiting: bool = True,
    ) -> HitlRequest:
        if sum(
            bool(value)
            for value in (run_id, execution_plan_run_id, execution_node_run_id)
        ) != 1:
            raise ValueError("HITL request requires exactly one durable owner")
        existing = await self.session.get(HitlRequest, hitl_request_id)
        if existing is not None:
            materialized = await self._materialize_request(existing)
            assert materialized is not None
            return materialized
        row = HitlRequest(
            hitl_request_id=hitl_request_id,
            tenant_id=_uuid(tenant_id),
            chat_id=chat_id,
            run_id=run_id or None,
            execution_plan_run_id=execution_plan_run_id or None,
            execution_node_run_id=execution_node_run_id or None,
            artifact_id=artifact_id,
            hitl_type=hitl_type,
        )
        row.title = title
        row.prompt_text = prompt_text
        row.ui_payload_json = _safe_json(ui_payload_json)
        row.agent_payload_json = _safe_json(agent_payload_json)
        row.decision_payload_json = {}
        row.runtime_correlation_json = _safe_json(runtime_correlation_json)
        row.resume_payload_json = _safe_json(resume_payload_json)
        row.interaction_result_json = {}
        await self._store_request_private(row)
        self.session.add(row)
        if run_id and mark_run_waiting:
            run = await self.session.get(AgentRun, run_id)
            if run is not None and run.status in {"running", "waiting_approval"}:
                run.status = "waiting_approval"
                run.updated_at = _now()
        await self.session.flush()
        return row

    async def link_artifact_hitl(self, artifact_id: str, hitl_request_id: str) -> None:
        artifact = await self.session.get(InteractiveArtifact, artifact_id)
        if artifact is not None:
            artifact.hitl_request_id = hitl_request_id
            artifact.updated_at = _now()
            await self.session.flush()

    async def get_request(self, hitl_request_id: str) -> HitlRequest | None:
        return await self._materialize_request(
            await self.session.get(HitlRequest, hitl_request_id)
        )

    async def mark_runtime_control_delivered(
        self,
        hitl_request_id: str,
    ) -> HitlRequest | None:
        row = (
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.hitl_request_id == hitl_request_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        row = await self._materialize_request(row)
        if row is None:
            return None
        resume = dict(row.resume_payload_json or {})
        resume["control_delivered"] = True
        resume["control_delivered_at"] = _now().isoformat()
        row.resume_payload_json = resume
        row.updated_at = _now()
        await self._store_request_private(row)
        await self.session.flush()
        return row

    async def get_request_for_user(
        self, hitl_request_id: str, user_id: str | uuid.UUID,
    ) -> HitlRequest | None:
        row = (
            await self.session.execute(
                select(HitlRequest)
                .join(Chat, Chat.chat_id == HitlRequest.chat_id)
                .where(
                    HitlRequest.hitl_request_id == hitl_request_id,
                    Chat.creator_user_id == _uuid(user_id),
                    Chat.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return await self._materialize_request(row)

    async def get_artifact(self, artifact_id: str) -> InteractiveArtifact | None:
        return await self._materialize_artifact(
            await self.session.get(InteractiveArtifact, artifact_id)
        )

    async def get_artifact_for_user(
        self, artifact_id: str, user_id: str | uuid.UUID,
    ) -> InteractiveArtifact | None:
        row = (
            await self.session.execute(
                select(InteractiveArtifact)
                .join(Chat, Chat.chat_id == InteractiveArtifact.chat_id)
                .where(
                    InteractiveArtifact.artifact_id == artifact_id,
                    Chat.creator_user_id == _uuid(user_id),
                    Chat.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        return await self._materialize_artifact(row)

    async def lock_artifact_for_user(
        self, artifact_id: str, user_id: str | uuid.UUID,
    ) -> InteractiveArtifact | None:
        """Lock an owned artifact before a VFS write/Continue race is decided."""
        row = (
            await self.session.execute(
                select(InteractiveArtifact)
                .join(Chat, Chat.chat_id == InteractiveArtifact.chat_id)
                .where(
                    InteractiveArtifact.artifact_id == artifact_id,
                    Chat.creator_user_id == _uuid(user_id),
                    Chat.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        return await self._materialize_artifact(row)

    async def record_artifact_result_file(
        self,
        *,
        artifact: InteractiveArtifact,
        result_file: dict,
    ) -> None:
        """Persist the latest Save reference while retaining prior distinct paths."""
        await self._materialize_artifact(artifact)
        current = _safe_json(artifact.interaction_result_json)
        saved_files = [
            item
            for item in current.get("saved_files", [])
            if isinstance(item, dict) and item.get("path") != result_file.get("path")
        ]
        saved_files.append(_safe_json(result_file))
        artifact.interaction_result_json = {
            **current,
            **_safe_json(result_file),
            "saved_files": saved_files,
        }
        artifact.updated_at = _now()
        await self._store_artifact_private(artifact)
        await self.session.flush()

    async def update_artifact_state_for_user(
        self,
        *,
        artifact_id: str,
        user_id: str | uuid.UUID,
        state: dict,
    ) -> tuple[InteractiveArtifact | None, bool]:
        """Persist a draft only while the artifact is still interactive."""
        row = (
            await self.session.execute(
                select(InteractiveArtifact)
                .join(Chat, Chat.chat_id == InteractiveArtifact.chat_id)
                .where(
                    InteractiveArtifact.artifact_id == artifact_id,
                    Chat.creator_user_id == _uuid(user_id),
                    Chat.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None, False
        await self._materialize_artifact(row)
        if row.is_interacted:
            return row, False
        row.widget_state_json = _safe_json(state)
        row.updated_at = _now()
        await self._store_artifact_private(row)
        await self.session.flush()
        return row, True

    async def get_request_by_artifact(self, artifact_id: str) -> HitlRequest | None:
        row = (
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.artifact_id == artifact_id)
                .order_by(HitlRequest.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return await self._materialize_request(row)

    async def list_artifact_refs_for_chat(
        self, chat_id: str,
    ) -> list[tuple[InteractiveArtifact, HitlRequest | None]]:
        rows = await self.session.execute(
            select(InteractiveArtifact, HitlRequest)
            .outerjoin(
                HitlRequest,
                HitlRequest.hitl_request_id == InteractiveArtifact.hitl_request_id,
            )
            .where(InteractiveArtifact.chat_id == chat_id)
            .order_by(InteractiveArtifact.created_at)
        )
        result = list(rows.all())
        for artifact, request in result:
            await self._materialize_artifact(artifact)
            await self._materialize_request(request)
        return result

    async def project_artifact_refs_for_chat(self, chat_id: str) -> dict[str, dict]:
        """Return the compact Runtime/checkpoint projection for a Chat.

        PostgreSQL is the product source of truth.  The host places this
        projection on every RuntimeTurnRequest so sandbox adapters never need a
        database credential merely to reconcile a completed interaction.
        """
        refs: dict[str, dict] = {}
        for artifact, request in await self.list_artifact_refs_for_chat(chat_id):
            interaction_result = _safe_json(artifact.interaction_result_json)
            if interaction_result.get("result_path"):
                widget_state = {
                    "result_path": interaction_result["result_path"],
                    "content_type": interaction_result.get("content_type"),
                    "hash": interaction_result.get("hash"),
                    "db_ref": f"interactive_artifact:{artifact.artifact_id}",
                }
            else:
                widget_state = _safe_json(artifact.widget_state_json)
            refs[artifact.artifact_id] = {
                "artifact_id": artifact.artifact_id,
                "hitl_request_id": artifact.hitl_request_id,
                "status": (
                    str(request.status)
                    if request is not None
                    else ("submitted" if artifact.is_interacted else "rendered")
                ),
                "content_hash": artifact.content_hash,
                "db_ref": f"interactive_artifact:{artifact.artifact_id}",
                "widget_state": widget_state,
            }
        return refs

    async def list_pending_for_chat(self, chat_id: str) -> list[HitlRequest]:
        rows = list((
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.chat_id == chat_id, HitlRequest.status == "pending")
                .order_by(HitlRequest.created_at)
            )
        ).scalars().all())
        for row in rows:
            await self._materialize_request(row)
        return rows

    async def list_pending_for_chat_user(
        self, chat_id: str, user_id: str | uuid.UUID,
    ) -> list[HitlRequest]:
        rows = list((
            await self.session.execute(
                select(HitlRequest)
                .join(Chat, Chat.chat_id == HitlRequest.chat_id)
                .where(
                    HitlRequest.chat_id == chat_id,
                    HitlRequest.status == "pending",
                    Chat.creator_user_id == _uuid(user_id),
                    Chat.deleted_at.is_(None),
                )
                .order_by(HitlRequest.created_at)
            )
        ).scalars().all())
        for row in rows:
            await self._materialize_request(row)
        return rows

    async def list_pending_for_run(self, run_id: str) -> list[HitlRequest]:
        rows = list((
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.run_id == run_id, HitlRequest.status == "pending")
                .order_by(HitlRequest.created_at)
            )
        ).scalars().all())
        for row in rows:
            await self._materialize_request(row)
        return rows

    async def resolve(
        self,
        *,
        hitl_request_id: str,
        decision: str,
        decision_payload: dict,
        interaction_result: dict | None = None,
        actor_id: str | None = None,
    ) -> tuple[HitlRequest | None, bool]:
        owner_probe = (
            await self.session.execute(
                select(
                    HitlRequest.execution_plan_run_id,
                    HitlRequest.hitl_type,
                ).where(HitlRequest.hitl_request_id == hitl_request_id)
            )
        ).one_or_none()
        if (
            owner_probe is not None
            and owner_probe.execution_plan_run_id
            and owner_probe.hitl_type == "plan_start_approval"
        ):
            return await self._resolve_plan_start(
                hitl_request_id=hitl_request_id,
                decision=decision,
                decision_payload=decision_payload,
                interaction_result=interaction_result,
                actor_id=actor_id,
            )
        # Multiple open frontends may submit the same card. Lock the durable
        # request so exactly one transition emits the resolution event; later
        # submissions receive the already-frozen terminal projection.
        req = (
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.hitl_request_id == hitl_request_id)
                .with_for_update()
                # A routing probe may already have placed this row in the
                # identity map. Refresh after the lock wait so a concurrent
                # winner's terminal status cannot remain stale as "pending".
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if req is None:
            return None, False
        await self._materialize_request(req)
        if req.status != "pending":
            return req, False
        now = _now()
        status = {
            "approve": "approved",
            "approved": "approved",
            "deny": "denied",
            "denied": "denied",
            "submit": "submitted",
            "submitted": "submitted",
            "cancel": "cancelled",
            "cancelled": "cancelled",
        }.get(decision, decision)
        req.status = status
        req.decision_payload_json = _safe_json(decision_payload)
        req.interaction_result_json = _safe_json(interaction_result or decision_payload)
        req.is_interacted = True
        req.resolved_at = now
        req.updated_at = now

        if req.artifact_id:
            artifact = await self.get_artifact(req.artifact_id)
            if artifact is not None:
                artifact.is_interacted = True
                artifact.interaction_result_json = req.interaction_result_json
                artifact.widget_state_json = _safe_json(decision_payload.get("widget_state"))
                artifact.updated_at = now
                await self._store_artifact_private(artifact)

        if req.run_id:
            run = await self.session.get(AgentRun, req.run_id)
            if run is not None:
                # Do not allocate agent_run_events.seq here. The live Turn's
                # AsyncTurnBuffer/AgentRunWriter owns that sequence space and
                # emits HITL_RESOLVED after the outer loop observes this durable
                # decision. Allocating last_event_id + 1 from the HTTP request
                # races the next streamed frame and can violate (run_id, seq).
                run.heartbeat_at = now
                if run.status == "waiting_approval":
                    run.status = "running"
                run.updated_at = now
        await self._store_request_private(req)
        await self.session.flush()
        return req, True

    async def _resolve_plan_start(
        self,
        *,
        hitl_request_id: str,
        decision: str,
        decision_payload: dict,
        interaction_result: dict | None,
        actor_id: str | None,
    ) -> tuple[HitlRequest | None, bool]:
        """Resolve Plan Start with Plan -> Run -> HITL lock ordering.

        This continuation is entirely database-owned; it never resumes the
        planner Turn and can therefore survive refreshes and worker restarts.
        """
        plan_run_id = (
            await self.session.execute(
                select(HitlRequest.execution_plan_run_id).where(
                    HitlRequest.hitl_request_id == hitl_request_id
                )
            )
        ).scalar_one_or_none()
        if not plan_run_id:
            return None, False
        plan_id = (
            await self.session.execute(
                select(ExecutionPlanRun.plan_id).where(
                    ExecutionPlanRun.plan_run_id == plan_run_id
                )
            )
        ).scalar_one_or_none()
        if not plan_id:
            return None, False
        plan = (
            await self.session.execute(
                select(ExecutionPlan)
                .where(ExecutionPlan.plan_id == plan_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        run = (
            await self.session.execute(
                select(ExecutionPlanRun)
                .where(ExecutionPlanRun.plan_run_id == plan_run_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        req = (
            await self.session.execute(
                select(HitlRequest)
                .where(HitlRequest.hitl_request_id == hitl_request_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if plan is None or run is None or req is None:
            return None, False
        await self._materialize_request(req)
        if req.status != "pending" or run.status != "awaiting_approval":
            return req, False

        normalized = {
            "approve": "approved",
            "approved": "approved",
            "deny": "denied",
            "denied": "denied",
            "cancel": "cancelled",
            "cancelled": "cancelled",
        }.get(decision, decision)
        if normalized not in {"approved", "denied", "cancelled"}:
            raise ValueError("Plan Start accepts only approve, deny, or cancel")
        now = _now()
        req.status = normalized
        req.decision_payload_json = _safe_json(decision_payload)
        req.interaction_result_json = _safe_json(
            interaction_result or decision_payload
        )
        req.is_interacted = True
        req.resolved_at = now
        req.updated_at = now

        if normalized == "approved":
            run.status = "queued"
            plan.lifecycle_status = "approved"
            start_node = (
                await self.session.execute(
                    select(ExecutionNodeRun)
                    .where(
                        ExecutionNodeRun.plan_run_id == run.plan_run_id,
                        ExecutionNodeRun.node_type == "start",
                        ExecutionNodeRun.status == "pending",
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if start_node is not None:
                start_node.status = "ready"
                start_node.updated_at = now
            event_type = "run_approved"
            event_payload = {"status": "queued", "approved_by": "user"}
            control_id = f"planctl_{uuid.uuid4().hex}"
            control_private = await content_encryption_service().encrypt_json(
                self.session,
                tenant_id=run.tenant_id,
                resource_type="chat",
                resource_id=run.chat_id,
                purpose="execution_plan_control",
                record_id=control_id,
                value={"decision": "approved", "hitl_request_id": hitl_request_id},
            )
            self.session.add(ExecutionPlanControl(
                control_id=control_id,
                tenant_id=run.tenant_id,
                plan_id=run.plan_id,
                plan_run_id=run.plan_run_id,
                action="approve_start",
                actor_type="user",
                actor_id=actor_id or "unknown_user",
                expected_revision=run.revision,
                idempotency_key=f"approve-start:{hitl_request_id}",
                hitl_request_id=hitl_request_id,
                delivery_status="applied",
                private_ciphertext=control_private.ciphertext,
                private_nonce=control_private.nonce,
                private_key_id=control_private.key_id,
                applied_at=now,
            ))
        else:
            run.status = "not_started"
            run.ended_at = now
            event_type = "run_not_started"
            event_payload = {
                "status": "not_started",
                "reason": "denied" if normalized == "denied" else "cancelled",
            }
        run.updated_at = now
        run.last_event_seq += 1
        event = await content_encryption_service().encrypt_json(
            self.session,
            tenant_id=run.tenant_id,
            resource_type="chat",
            resource_id=run.chat_id,
            purpose="execution_plan_run_event",
            record_id=f"{run.plan_run_id}:{run.last_event_seq}",
            value=event_payload,
        )
        self.session.add(ExecutionPlanRunEvent(
            plan_run_id=run.plan_run_id,
            seq=run.last_event_seq,
            tenant_id=run.tenant_id,
            event_type=event_type,
            payload_ciphertext=event.ciphertext,
            payload_nonce=event.nonce,
            payload_key_id=event.key_id,
        ))
        await self._store_request_private(req)
        await self.session.flush()
        return req, True

    async def set_interaction_result(
        self,
        *,
        hitl_request_id: str,
        interaction_result: dict,
    ) -> HitlRequest | None:
        req = await self.get_request(hitl_request_id)
        if req is None:
            return None
        now = _now()
        req.interaction_result_json = _safe_json(interaction_result)
        req.updated_at = now
        if req.artifact_id:
            artifact = await self.get_artifact(req.artifact_id)
            if artifact is not None:
                artifact.interaction_result_json = req.interaction_result_json
                artifact.updated_at = now
                await self._store_artifact_private(artifact)
        await self._store_request_private(req)
        await self.session.flush()
        return req
