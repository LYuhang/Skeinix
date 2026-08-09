"""Durable identities for non-interactive Workflow execution roots."""

from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class ServiceAccount(Base):
    __tablename__ = "service_accounts"

    service_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    owner_resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="active", server_default="active",
    )
    generation: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1"),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        onupdate=func.now(),
    )
    disabled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    __table_args__ = (
        CheckConstraint(
            "kind IN ('deployment', 'schedule', 'task', 'integration')",
            name="ck_service_accounts_kind",
        ),
        CheckConstraint(
            "owner_resource_type IN ('deployment', 'task', 'integration')",
            name="ck_service_accounts_owner_resource_type",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'deleted')",
            name="ck_service_accounts_status",
        ),
        CheckConstraint("generation > 0", name="ck_service_accounts_generation"),
        UniqueConstraint(
            "tenant_id", "owner_resource_type", "owner_resource_id",
            name="uq_service_accounts_owner",
        ),
        Index("ix_service_accounts_tenant_status", "tenant_id", "status", "updated_at"),
    )


class ServiceAccountCredential(Base):
    __tablename__ = "service_account_credentials"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    service_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("service_accounts.service_account_id", ondelete="CASCADE"),
        primary_key=True,
    )
    credential_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_credentials.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
