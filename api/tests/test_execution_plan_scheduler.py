from __future__ import annotations

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.services.execution_plans.scheduler import ExecutionPlanScheduler
from vibecanvas_api.services.execution_plans.validator import validate_plan_bytes
from vibecanvas_api.storage.chat_repo import ChatRepo
from vibecanvas_api.storage.execution_plan_repo import ExecutionPlanRepo
from vibecanvas_api.storage.hitl_repo import HitlRepo
from vibecanvas_api.storage.agent_runs_repo import AgentRunsRepo
from vibecanvas_api.storage.models_agent_runs import HitlRequest
from vibecanvas_api.storage.models_execution_plans import (
    ExecutionNodeAttempt,
    ExecutionNodeOutput,
    ExecutionNodeRun,
    ExecutionPlanRun,
    ExecutionPlanRunEvent,
)
from vibecanvas_api.storage.workflow_repo import WorkflowRepo
from vibecanvas_api.storage.repo_llm_credentials import LlmCredentialsRepo


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def stream(self, request):
        runtime = request["runtime_request"]
        job_id = runtime["job_id"]
        self.calls[job_id] = self.calls.get(job_id, 0) + 1
        yield {"kind": "event", "progress": {"current": 1, "total": 1, "message": "Producing typed output"}}
        title = runtime["title"]
        if title == "Choose route":
            result = {"decision": "go"}
        elif title == "Repair me" and self.calls[job_id] == 1:
            result = {"answer": 7}
        elif title == "Repair me":
            result = {"answer": "fixed"}
        else:
            result = {"text": f"done:{title}"}
        trace = {"role": "assistant", "text": f"Finished {title}", "tool_calls": []}
        yield {"kind": "event", "trace_entry": trace}
        yield {
            "kind": "result",
            "status": "done",
            "result": result,
            "trace": [trace],
        }


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(self, request):
        yield {"kind": "event", "progress": {"current": 1, "total": None, "message": "Working"}}
        self.started.set()
        await asyncio.Event().wait()


class ApprovalExecutor:
    def __init__(self) -> None:
        self.requested = asyncio.Event()
        self.control = asyncio.Event()
        self.response: dict | None = None

    async def stream(self, request):
        job_id = request["runtime_request"]["job_id"]
        yield {
            "kind": "event",
            "approval": {
                "hitl_request_id": f"hitl-node-{job_id}",
                "title": "Approve bash",
                "prompt_text": "Allow this Plan subagent to execute bash?",
                "tool_name": "bash",
                "arguments": {"command": "printf safe"},
                "runtime_correlation": {
                    "source": "plan-node",
                    "runtime_request_id": "tool-bash-1",
                    "job_id": job_id,
                },
            },
        }
        self.requested.set()
        await self.control.wait()
        assert self.response is not None and self.response["action"] == "approve"
        yield {"kind": "result", "status": "done", "result": {"answer": "approved"}, "trace": []}

    async def send_control(self, request):
        self.response = request["response"]
        self.control.set()
        return True


async def _seed(app_engine, tenant, user):
    async with app_engine.begin() as connection:
        await connection.execute(text("INSERT INTO tenants(tenant_id,name) VALUES (:t,'scheduler')"), {"t": tenant})
        await connection.execute(text("INSERT INTO users(user_id,tenant_id,email) VALUES (:u,:t,:e)"), {"u": user, "t": tenant, "e": f"{user.hex[:8]}@scheduler.test"})
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        await WorkflowRepo(session, str(user)).create_workflow(wf_id="wf_scheduler", name="Scheduler")
        await ChatRepo(session, str(user)).register_session("wf_scheduler", name="Scheduler chat", chat_id="chat_scheduler")
        await session.commit()


def _report():
    source = {
        "schema_version": 1,
        "title": "Static fork and join",
        "nodes": [
            {"id": "start", "type": "start", "next": ["choose"]},
            {
                "id": "choose", "type": "subagent", "title": "Choose route",
                "task": "Prepare shared context at /data/plan-work/context.md.",
                "next": ["branch_a", "branch_b"],
            },
            {
                "id": "branch_a", "type": "subagent", "title": "Branch A",
                "task": "Read /data/plan-work/context.md and write /data/plan-work/a.md.",
                "next": ["end"],
            },
            {
                "id": "branch_b", "type": "subagent", "title": "Branch B",
                "task": "Read /data/plan-work/context.md and write /data/plan-work/b.md.",
                "next": ["end"],
            },
            {"id": "end", "type": "end"},
        ],
        "budgets": {"max_wall_time_seconds": 300},
    }
    report = validate_plan_bytes("/data/plans/scheduler.plan.json", json.dumps(source).encode())
    assert report.status == "valid", report.errors
    return report


def _repair_report():
    source = {
        "schema_version": 1,
        "title": "Repair output",
        "nodes": [
            {"id": "start", "type": "start", "next": ["repair"]},
            {"id": "repair", "type": "subagent", "title": "Repair me", "task": "Return a concise result.", "next": ["end"]},
            {"id": "end", "type": "end"},
        ],
        "budgets": {"max_wall_time_seconds": 300},
    }
    report = validate_plan_bytes("/data/plans/repair.plan.json", json.dumps(source).encode())
    assert report.status == "valid"
    return report


async def _drain(scheduler: ExecutionPlanScheduler):
    while scheduler._tasks:
        await asyncio.gather(*list(scheduler._tasks.values()), return_exceptions=False)
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_plan_node_inherits_the_authoring_turn_model_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credential_id = uuid.uuid4()
    monkeypatch.setattr(
        AgentRunsRepo,
        "get",
        AsyncMock(return_value=SimpleNamespace(input_snapshot={
            "model_id": f"langchain:credential:{credential_id}",
            "reasoning_effort": "low",
        })),
    )
    monkeypatch.setattr(
        LlmCredentialsRepo,
        "get_for_user",
        AsyncMock(return_value={
            "id": credential_id,
            "provider": "openai",
            "model_name": "gpt-5-mini",
            "updated_at": "2026-08-10T00:00:00Z",
        }),
    )
    run = SimpleNamespace(
        tenant_id=uuid.uuid4(),
        creator_user_id=uuid.uuid4(),
        chat_id="chat-model-binding",
        plan_run_id="planrun-model-binding",
        create_turn_id="turn-model-binding",
        budget_json={"max_wall_time_seconds": 300},
        approval_mode_snapshot="always_allow",
    )
    node = SimpleNamespace(node_run_id="node-model-binding", node_path="work")

    request = await ExecutionPlanScheduler._runtime_request(
        object(), run, node, {"title": "Work", "task": "Return OK."},
    )

    assert request["model"]["id"] == "gpt-5-mini"
    assert request["model"]["reasoning"] == {"effort": "low"}
    assert request["model"]["base_url"].endswith("/api/internal/runtime-model/v1")
    assert request["model"]["api_key"]


@pytest.mark.asyncio
async def test_scheduler_executes_static_fork_join_and_persists_events(app_engine):
    tenant, user = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, tenant, user)
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        submission = await ExecutionPlanRepo(session).create_validated(
            tenant_id=str(tenant), chat_id="chat_scheduler", creator_user_id=str(user),
            parent_turn_id="turn-scheduler", tool_invocation_id="tool-scheduler",
            approval_mode="always_allow", authorization_snapshot_hash="sha256:authz", report=_report(),
        )
        await session.commit()
    scheduler = ExecutionPlanScheduler(FakeExecutor())
    for _ in range(8):
        await scheduler.tick_tenant(str(tenant))
        await _drain(scheduler)
        async with AsyncSession(app_engine) as session:
            await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
            run = await session.get(ExecutionPlanRun, submission.plan_run_id)
            if run and run.status == "completed":
                break
    assert run is not None and run.status == "completed"
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        nodes = list((await session.execute(select(ExecutionNodeRun).where(ExecutionNodeRun.plan_run_id == submission.plan_run_id))).scalars().all())
        assert {row.node_path for row in nodes} == {"start", "choose", "branch_a", "branch_b", "end"}
        assert all(row.status == "succeeded" for row in nodes)
        events = list((await session.execute(select(ExecutionPlanRunEvent).where(ExecutionPlanRunEvent.plan_run_id == submission.plan_run_id))).scalars().all())
        assert {row.event_type for row in events} >= {"run_started", "node_progress", "node_output_delta", "node_result_committed", "run_completed"}
        outputs = list((await session.execute(
            select(ExecutionNodeOutput).join(
                ExecutionNodeRun,
                ExecutionNodeRun.node_run_id == ExecutionNodeOutput.node_run_id,
            ).where(ExecutionNodeRun.plan_run_id == submission.plan_run_id)
        )).scalars().all())
        assert len(outputs) == 6


@pytest.mark.asyncio
async def test_scheduler_accepts_generic_output_without_automatic_second_attempt(app_engine):
    tenant, user = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, tenant, user)
    fake = FakeExecutor()
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        submission = await ExecutionPlanRepo(session).create_validated(
            tenant_id=str(tenant), chat_id="chat_scheduler", creator_user_id=str(user),
            parent_turn_id="turn-repair", tool_invocation_id="tool-repair",
            approval_mode="always_allow", authorization_snapshot_hash="sha256:authz", report=_repair_report(),
        )
        await session.commit()
    scheduler = ExecutionPlanScheduler(fake)
    for _ in range(6):
        await scheduler.tick_tenant(str(tenant)); await _drain(scheduler)
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        run = await session.get(ExecutionPlanRun, submission.plan_run_id)
        worker = (await session.execute(select(ExecutionNodeRun).where(ExecutionNodeRun.plan_run_id == submission.plan_run_id, ExecutionNodeRun.node_path == "repair"))).scalar_one()
        attempts = list((await session.execute(select(ExecutionNodeAttempt).where(ExecutionNodeAttempt.node_run_id == worker.node_run_id).order_by(ExecutionNodeAttempt.attempt))).scalars().all())
        assert run is not None and run.status == "completed"
        assert [row.status for row in attempts] == ["succeeded"]
        assert fake.calls[worker.node_run_id] == 1


@pytest.mark.asyncio
async def test_running_node_cancel_stops_only_target_and_records_unknown_side_effects(app_engine):
    tenant, user = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, tenant, user)
    blocker = BlockingExecutor()
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        submission = await ExecutionPlanRepo(session).create_validated(
            tenant_id=str(tenant), chat_id="chat_scheduler", creator_user_id=str(user),
            parent_turn_id="turn-cancel", tool_invocation_id="tool-cancel-running",
            approval_mode="always_allow", authorization_snapshot_hash="sha256:authz", report=_repair_report(),
        )
        await session.commit()
    scheduler = ExecutionPlanScheduler(blocker)
    await scheduler.tick_tenant(str(tenant))
    await asyncio.wait_for(blocker.started.wait(), timeout=3)
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        worker = (await session.execute(select(ExecutionNodeRun).where(
            ExecutionNodeRun.plan_run_id == submission.plan_run_id,
            ExecutionNodeRun.node_path == "repair",
        ))).scalar_one()
        assert worker.status == "running"
        await ExecutionPlanRepo(session).request_node_cancel(
            node_run_id=worker.node_run_id, actor_id=str(user),
            reason="user_requested", idempotency_key="cancel-running-node",
        )
        await session.commit()
    await scheduler.tick_tenant(str(tenant))
    await _drain(scheduler)
    await scheduler.tick_tenant(str(tenant))
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        run = await session.get(ExecutionPlanRun, submission.plan_run_id)
        worker = (await session.execute(select(ExecutionNodeRun).where(
            ExecutionNodeRun.plan_run_id == submission.plan_run_id,
            ExecutionNodeRun.node_path == "repair",
        ))).scalar_one()
        events = list((await session.execute(select(ExecutionPlanRunEvent).where(
            ExecutionPlanRunEvent.plan_run_id == submission.plan_run_id,
        ))).scalars().all())
        assert worker.status == "cancelled"
        assert worker.side_effect_state == "unknown"
        assert run is not None and run.status == "cancelled"
        assert {event.event_type for event in events} >= {
            "node_cancel_requested", "node_cancel_signal_delivered", "node_cancelled",
        }


@pytest.mark.asyncio
async def test_node_tool_approval_is_independent_durable_and_node_correlated(app_engine):
    tenant, user = uuid.uuid4(), uuid.uuid4()
    await _seed(app_engine, tenant, user)
    executor = ApprovalExecutor()
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        submission = await ExecutionPlanRepo(session).create_validated(
            tenant_id=str(tenant), chat_id="chat_scheduler", creator_user_id=str(user),
            parent_turn_id="turn-approval", tool_invocation_id="tool-plan-approval",
            approval_mode="always_ask", authorization_snapshot_hash="sha256:authz", report=_repair_report(),
        )
        plan_start = (await session.execute(select(HitlRequest).where(
            HitlRequest.execution_plan_run_id == submission.plan_run_id,
        ))).scalar_one()
        await HitlRepo(session).resolve(
            hitl_request_id=plan_start.hitl_request_id,
            decision="approve", decision_payload={}, actor_id=str(user),
        )
        await session.commit()
    scheduler = ExecutionPlanScheduler(executor)
    await scheduler.tick_tenant(str(tenant))
    await asyncio.wait_for(executor.requested.wait(), timeout=3)
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        approval = (await session.execute(select(HitlRequest).where(
            HitlRequest.hitl_type == "plan_node_tool_approval",
        ))).scalar_one()
        node_approval_id = approval.hitl_request_id
        node = await session.get(ExecutionNodeRun, approval.execution_node_run_id)
        assert node is not None and node.attention_status == "waiting_tool_approval"
        await HitlRepo(session).resolve(
            hitl_request_id=approval.hitl_request_id,
            decision="approve", decision_payload={}, actor_id=str(user),
        )
        await session.commit()
    await scheduler.tick_tenant(str(tenant))
    await _drain(scheduler)
    for _ in range(3):
        await scheduler.tick_tenant(str(tenant))
    async with AsyncSession(app_engine) as session:
        await session.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant)})
        approval = await HitlRepo(session).get_request(node_approval_id)
        run = await session.get(ExecutionPlanRun, submission.plan_run_id)
        assert approval is not None and approval.resume_payload_json["control_delivered"] is True
        assert run is not None and run.status == "completed"
        events = list((await session.execute(select(ExecutionPlanRunEvent).where(
            ExecutionPlanRunEvent.plan_run_id == submission.plan_run_id,
        ))).scalars().all())
        assert {event.event_type for event in events} >= {"node_attention_changed", "node_succeeded"}
