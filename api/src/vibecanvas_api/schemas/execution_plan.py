"""Public product contracts for Dynamic Execution Plan previews."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExecutionPlanControlBody(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="user_requested", max_length=256)


class ExecutionPlanCardOut(BaseModel):
    plan_id: str
    plan_run_id: str
    job_id: str
    chat_id: str
    revision: int
    title: str
    status: str
    node_count: int
    parallel_branch_count: int
    progress: dict[str, Any] = Field(default_factory=dict)
    last_event_seq: int = 0
    created_at: str | None = None
    updated_at: str | None = None
    preview_resource: dict[str, Any]


class ExecutionPlanDetailOut(BaseModel):
    plan_id: str
    chat_id: str
    revision: int
    lifecycle_status: str
    definition: dict[str, Any]
    validation: dict[str, Any] = Field(default_factory=dict)
    source_plan_path: str
    definition_hash: str
    created_at: str | None = None
    runs: list[dict[str, Any]] = Field(default_factory=list)


class ExecutionPlanRunOut(BaseModel):
    plan_run_id: str
    job_id: str
    plan_id: str
    revision: int
    chat_id: str
    status: str
    approval_mode: str
    budget: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    last_event_seq: int = 0
    cancel_requested: bool = False
    started_at: str | None = None
    ended_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    approval: dict[str, Any] | None = None


class ExecutionNodeRunOut(BaseModel):
    node_run_id: str
    plan_run_id: str
    chat_id: str
    node_path: str
    node_type: str
    status: str
    attention_status: str
    current_attempt: int
    current_activity: str = ""
    definition: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    output_ref: str | None = None
    error: dict[str, Any] = Field(default_factory=dict)
    side_effect_state: str
    progress: dict[str, Any] = Field(default_factory=dict)
    cancel_requested: bool = False
    started_at: str | None = None
    ended_at: str | None = None
    updated_at: str | None = None
    approval: dict[str, Any] | None = None
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    output: list[dict[str, Any]] = Field(default_factory=list)
