from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.services.execution_plans.validator import validate_plan_bytes
from vibecanvas_api.services.background_job_registry import (
    cancel_background_job,
    get_background_job,
    list_background_jobs,
    list_background_jobs_page,
)
from vibecanvas_api.services.execution_plans.projection import (
    ExecutionPlanProjectionService,
)
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.execution_plan_repo import ExecutionPlanRepo
from vibecanvas_api.storage.hitl_repo import HitlRepo
from vibecanvas_api.storage.models_agent_runs import HitlRequest
from vibecanvas_api.storage.models_execution_plans import (
    ExecutionNodeRun,
    ExecutionPlan,
    ExecutionPlanControlDelivery,
    ExecutionPlanRevision,
    ExecutionPlanRun,
)
from vibecanvas_api.storage.workflow_repo import WorkflowRepo


def _report(title: str = "Durable plan"):
    source = {
        "schema_version": 1,
        "title": title,
        "nodes": [
            {"id": "start", "type": "start", "next": ["finish"]},
            {"id": "finish", "type": "end"},
        ],
        "budgets": {
            "max_wall_time_seconds": 300,
        },
    }
    report = validate_plan_bytes(
        "/data/plans/durable.plan.json",
        json.dumps(source).encode(),
    )
    assert report.status == "valid"
    return report


def _subagent_report():
    source = {
        "schema_version": 1,
        "title": "Cancelable plan",
        "nodes": [
            {"id": "start", "type": "start", "next": ["research"]},
            {
                "id": "research",
                "type": "subagent",
                "title": "Research",
                "task": "Collect durable evidence and write /data/plan-work/research.md.",
                "next": ["finish"],
            },
            {"id": "finish", "type": "end"},
        ],
        "budgets": {
            "max_wall_time_seconds": 300,
        },
    }
    report = validate_plan_bytes(
        "/data/plans/cancel.plan.json", json.dumps(source).encode(),
    )
    assert report.status == "valid"
    return report


async def _seed(app_engine, tenant: uuid.UUID, user: uuid.UUID) -> None:
    async with app_engine.begin() as connection:
        await connection.execute(
            text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'plans')"),
            {"t": tenant},
        )
        await connection.execute(
            text(
                "INSERT INTO users(user_id,tenant_id,email) "
                "VALUES (:u,:t,:e)"
            ),
            {"u": user, "t": tenant, "e": f"{user.hex[:8]}@plans.test"},
        )
    async with AsyncSession(app_engine) as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant)},
        )
        await WorkflowRepo(session, str(user)).create_workflow(
            wf_id="wf_plan", name="Plan workspace",
        )
        await ChatRepo(session, str(user)).register_session(
            "wf_plan", name="Plan chat", chat_id="chat_plan",
        )
        await session.commit()


@pytest.mark.asyncio
async def test_valid_create_is_idempotent_and_approval_queues_same_run(app_engine):
    tenant, user = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, tenant, user)

    async with AsyncSession(app_engine) as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant)},
        )
        repo = ExecutionPlanRepo(session)
        first = await repo.create_validated(
            tenant_id=str(tenant),
            chat_id="chat_plan",
            creator_user_id=str(user),
            parent_turn_id="turn-plan",
            tool_invocation_id="tool-plan-create",
            approval_mode="always_ask",
            authorization_snapshot_hash="sha256:authz",
            report=_report(),
        )
        await session.commit()
        assert first.status == "awaiting_approval"
        assert first.job_id.startswith("job_plan_")

        replay = await repo.create_validated(
            tenant_id=str(tenant),
            chat_id="chat_plan",
            creator_user_id=str(user),
            parent_turn_id="turn-plan",
            tool_invocation_id="tool-plan-create",
            approval_mode="always_ask",
            authorization_snapshot_hash="sha256:authz",
            report=_report(),
        )
        assert replay.created is False
        assert replay.job_id == first.job_id
        assert replay.plan_run_id == first.plan_run_id

        assert len((await session.execute(select(ExecutionPlan))).scalars().all()) == 1
        assert len((await session.execute(select(ExecutionPlanRevision))).scalars().all()) == 1
        nodes = (
            await session.execute(
                select(ExecutionNodeRun).where(
                    ExecutionNodeRun.plan_run_id == first.plan_run_id
                )
            )
        ).scalars().all()
        assert {node.status for node in nodes} == {"pending"}
        hitl = (
            await session.execute(
                select(HitlRequest).where(
                    HitlRequest.execution_plan_run_id == first.plan_run_id
                )
            )
        ).scalar_one()
        request_id = hitl.hitl_request_id

        resolved, changed = await HitlRepo(session).resolve(
            hitl_request_id=request_id,
            decision="approve",
            decision_payload={"actor": str(user)},
        )
        assert changed is True
        assert resolved is not None and resolved.status == "approved"
        await session.commit()
        run = await session.get(ExecutionPlanRun, first.plan_run_id)
        plan = await session.get(ExecutionPlan, first.plan_id)
        assert run is not None and run.status == "queued"
        assert plan is not None and plan.lifecycle_status == "approved"
        start = (
            await session.execute(
                select(ExecutionNodeRun).where(
                    ExecutionNodeRun.plan_run_id == first.plan_run_id,
                    ExecutionNodeRun.node_type == "start",
                )
            )
        ).scalar_one()
        assert start.status == "ready"
        approval_controls = await repo.claim_control_projections(
            chat_id="chat_plan",
            creator_user_id=str(user),
            delivered_to_turn_id="turn-after-plan-approval",
        )
        assert len(approval_controls) == 1
        assert approval_controls[0]["action"] == "approve_start"
        assert approval_controls[0]["status"] == "queued"
        assert approval_controls[0]["projection_version"] == 2

        projector = ExecutionPlanProjectionService(session, user_id=str(user))
        plan_view = await projector.get_plan(plan_id=first.plan_id)
        assert plan_view is not None
        assert plan_view["definition"]["title"] == "Durable plan"
        run_view = await projector.get_run(plan_run_id=first.plan_run_id)
        assert run_view is not None
        assert run_view["approval"]["hitl_request_id"] == request_id
        assert {item["node_type"] for item in run_view["nodes"]} == {"start", "end"}
        node_view = await projector.get_node(node_run_id=start.node_run_id)
        assert node_view is not None
        assert node_view["definition"]["type"] == "start"
        events = await projector.list_events(
            plan_run_id=first.plan_run_id, after=0,
        )
        assert events is not None and events[0]["event_type"] == "run_created"

        detail = await get_background_job(
            session,
            chat_id="chat_plan",
            job_id=first.job_id,
            creator_user_id=str(user),
        )
        assert detail is not None
        assert detail["provider"] == "execution_plan"
        assert detail["status"] == "queued"
        listed = await list_background_jobs(
            session,
            chat_id="chat_plan",
            creator_user_id=str(user),
            include_finished=False,
            limit=20,
        )
        assert [item["job_id"] for item in listed] == [first.job_id]

        cancelled = await cancel_background_job(
            session,
            chat_id="chat_plan",
            job_id=first.job_id,
            creator_user_id=str(user),
        )
        await session.commit()
        assert cancelled is not None and cancelled["status"] == "cancelled"

        next_revision = await repo.create_validated(
            tenant_id=str(tenant),
            chat_id="chat_plan",
            creator_user_id=str(user),
            parent_turn_id="turn-plan-revised",
            tool_invocation_id="tool-plan-create-revised",
            approval_mode="always_allow",
            authorization_snapshot_hash="sha256:authz-next",
            report=_report("Durable plan revised"),
        )
        await session.commit()
        assert next_revision.plan_id == first.plan_id
        assert next_revision.revision == 2
        assert next_revision.plan_run_id != first.plan_run_id
        assert next_revision.job_id != first.job_id
        first_page = await list_background_jobs_page(
            session,
            chat_id="chat_plan",
            creator_user_id=str(user),
            include_finished=True,
            limit=1,
            cursor=None,
        )
        assert [item["job_id"] for item in first_page["jobs"]] == [
            next_revision.job_id,
        ]
        assert first_page["next_cursor"]
        second_page = await list_background_jobs_page(
            session,
            chat_id="chat_plan",
            creator_user_id=str(user),
            include_finished=True,
            limit=1,
            cursor=first_page["next_cursor"],
        )
        assert [item["job_id"] for item in second_page["jobs"]] == [first.job_id]


@pytest.mark.asyncio
async def test_node_cancel_is_idempotent_and_visible_in_projection(app_engine):
    tenant, user = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, tenant, user)
    async with AsyncSession(app_engine) as session:
        await session.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant)},
        )
        repo = ExecutionPlanRepo(session)
        submission = await repo.create_validated(
            tenant_id=str(tenant),
            chat_id="chat_plan",
            creator_user_id=str(user),
            parent_turn_id="turn-node-cancel",
            tool_invocation_id="tool-node-cancel",
            approval_mode="always_allow",
            authorization_snapshot_hash="sha256:authz",
            report=_subagent_report(),
        )
        worker = (
            await session.execute(
                select(ExecutionNodeRun).where(
                    ExecutionNodeRun.plan_run_id == submission.plan_run_id,
                    ExecutionNodeRun.node_path == "research",
                )
            )
        ).scalar_one()
        worker_id = worker.node_run_id
        await repo.request_node_cancel(
            node_run_id=worker_id,
            actor_id=str(user),
            reason="user_requested",
            idempotency_key="cancel-node-1",
        )
        replay = await repo.request_node_cancel(
            node_run_id=worker_id,
            actor_id=str(user),
            reason="user_requested",
            idempotency_key="cancel-node-1",
        )
        assert replay.status == "cancelled"
        controls = await repo.claim_control_projections(
            chat_id="chat_plan",
            creator_user_id=str(user),
            delivered_to_turn_id="turn-after-cancel",
        )
        assert len(controls) == 1
        assert controls[0] == {
            "control_id": controls[0]["control_id"],
            "projection_version": 2,
            "action": "cancel_node",
            "plan_id": submission.plan_id,
            "plan_run_id": submission.plan_run_id,
            "node_run_id": worker_id,
            "node_path": "research",
            "status": "cancelled",
            "reason": "user_requested",
            "side_effect_state": "none",
        }
        assert await repo.claim_control_projections(
            chat_id="chat_plan",
            creator_user_id=str(user),
            delivered_to_turn_id="turn-after-cancel-retry",
        ) == []
        await session.commit()
        delivery = await session.get(
            ExecutionPlanControlDelivery,
            (controls[0]["control_id"], 2),
        )
        assert delivery is not None
        assert delivery.delivered_to_turn_id == "turn-after-cancel"
        projected = await ExecutionPlanProjectionService(
            session, user_id=str(user),
        ).get_node(node_run_id=worker_id)
        assert projected is not None
        assert projected["status"] == "cancelled"
        assert projected["cancel_requested"] is True
