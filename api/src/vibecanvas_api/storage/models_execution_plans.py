"""Independent durable execution domain for Dynamic Execution Plans."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .models import Base


def _tenant_column():
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
        server_default=text("current_setting('app.tenant_id', true)::uuid"),
    )


def _key_column():
    return mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_encryption_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    )


class ExecutionPlan(Base):
    __tablename__ = "execution_plans"

    plan_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    chat_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False,
    )
    creator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    lifecycle_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="draft",
    )
    last_plan_event_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "lifecycle_status IN ('draft','approved','archived')",
            name="ck_execution_plans_lifecycle",
        ),
        Index(
            "ix_execution_plans_chat_updated",
            "tenant_id", "chat_id", "updated_at",
        ),
    )


class ExecutionPlanRevision(Base):
    __tablename__ = "execution_plan_revisions"

    plan_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("execution_plans.plan_id", ondelete="CASCADE"),
        primary_key=True,
    )
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_hash: Mapped[str] = mapped_column(Text, nullable=False)
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = _key_column()
    source_plan_path: Mapped[str] = mapped_column(Text, nullable=False)
    source_content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="valid",
    )
    node_count: Mapped[int] = mapped_column(Integer, nullable=False)
    parallel_branch_count: Mapped[int] = mapped_column(Integer, nullable=False)
    planner_runtime_type: Mapped[str] = mapped_column(Text, nullable=False)
    parent_turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "planner_runtime_type = 'langchain'",
            name="ck_execution_plan_revisions_runtime",
        ),
        UniqueConstraint(
            "tenant_id", "plan_id", "definition_hash",
            name="uq_execution_plan_revision_definition",
        ),
    )


class ExecutionPlanRun(Base):
    __tablename__ = "execution_plan_runs"

    plan_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    background_job_id: Mapped[str] = mapped_column(Text, nullable=False)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    plan_id: Mapped[str] = mapped_column(Text, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    chat_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False,
    )
    creator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    create_turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    create_tool_invocation_id: Mapped[str] = mapped_column(Text, nullable=False)
    approval_mode_snapshot: Mapped[str] = mapped_column(Text, nullable=False)
    executor_policy: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    approval_control_id: Mapped[str | None] = mapped_column(Text)
    budget_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    progress_summary_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    last_event_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="0",
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "revision"],
            ["execution_plan_revisions.plan_id", "execution_plan_revisions.revision"],
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "tenant_id", "background_job_id",
            name="uq_execution_plan_runs_background_job",
        ),
        UniqueConstraint(
            "chat_id", "create_tool_invocation_id",
            name="uq_execution_plan_runs_create_invocation",
        ),
        CheckConstraint(
            "status IN ('awaiting_approval','queued','running','completed',"
            "'failed','cancel_requested','cancelled','not_started')",
            name="ck_execution_plan_runs_status",
        ),
        Index(
            "ix_execution_plan_runs_plan_created",
            "tenant_id", "plan_id", "created_at",
        ),
    )


class ExecutionNodeRun(Base):
    __tablename__ = "execution_node_runs"

    node_run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    plan_run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("execution_plan_runs.plan_run_id", ondelete="CASCADE"),
        nullable=False,
    )
    node_path: Mapped[str] = mapped_column(Text, nullable=False)
    node_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    attention_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    current_attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    executor_runtime_type: Mapped[str | None] = mapped_column(Text)
    input_hash: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(Text)
    side_effect_state: Mapped[str] = mapped_column(Text, nullable=False, server_default="none")
    progress_current: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    progress_total: Mapped[int | None] = mapped_column(Integer)
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = _key_column()
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "plan_run_id", "node_path", name="uq_execution_node_runs_path",
        ),
        CheckConstraint(
            "status IN ('pending','ready','queued','running','succeeded','failed',"
            "'cancel_requested','cancelled','skipped')",
            name="ck_execution_node_runs_status",
        ),
        Index(
            "ix_execution_node_runs_scheduler",
            "tenant_id", "plan_run_id", "status", "updated_at",
        ),
    )


class ExecutionNodeAttempt(Base):
    __tablename__ = "execution_node_attempts"

    node_run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("execution_node_runs.node_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    status: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = _key_column()
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output_ref: Mapped[str | None] = mapped_column(Text)
    usage_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_execution_node_attempts_idempotency"),
        Index("ix_execution_node_attempts_lease", "tenant_id", "status", "lease_expires_at"),
    )


class ExecutionNodeOutput(Base):
    __tablename__ = "execution_node_outputs"

    node_run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("execution_node_runs.node_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    output_kind: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    payload_key_id: Mapped[uuid.UUID] = _key_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class ExecutionPlanControl(Base):
    __tablename__ = "execution_plan_controls"

    control_id: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    plan_id: Mapped[str] = mapped_column(
        Text, ForeignKey("execution_plans.plan_id", ondelete="CASCADE"), nullable=False,
    )
    plan_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("execution_plan_runs.plan_run_id", ondelete="CASCADE"),
    )
    node_run_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("execution_node_runs.node_run_id", ondelete="CASCADE"),
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    tool_invocation_id: Mapped[str | None] = mapped_column(Text)
    hitl_request_id: Mapped[str | None] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = _key_column()
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("plan_id", "idempotency_key", name="uq_execution_plan_controls_idempotency"),
        Index("ix_execution_plan_controls_run", "tenant_id", "plan_run_id", "requested_at"),
    )


class ExecutionPlanEvent(Base):
    __tablename__ = "execution_plan_events"

    plan_id: Mapped[str] = mapped_column(
        Text, ForeignKey("execution_plans.plan_id", ondelete="CASCADE"), primary_key=True,
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    plan_run_id: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    payload_key_id: Mapped[uuid.UUID] = _key_column()
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class ExecutionPlanRunEvent(Base):
    __tablename__ = "execution_plan_run_events"

    plan_run_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("execution_plan_runs.plan_run_id", ondelete="CASCADE"),
        primary_key=True,
    )
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    node_run_id: Mapped[str | None] = mapped_column(Text)
    attempt: Mapped[int | None] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    payload_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    payload_key_id: Mapped[uuid.UUID] = _key_column()
    trace_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class ExecutionPlanControlDelivery(Base):
    __tablename__ = "execution_plan_control_deliveries"

    control_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("execution_plan_controls.control_id", ondelete="CASCADE"),
        primary_key=True,
    )
    projection_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = _tenant_column()
    chat_id: Mapped[str] = mapped_column(
        Text, ForeignKey("chats.chat_id", ondelete="CASCADE"), nullable=False,
    )
    delivered_to_turn_id: Mapped[str] = mapped_column(Text, nullable=False)
    delivered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
