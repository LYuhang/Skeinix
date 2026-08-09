"""Time-bounded privileged-support requests and activation state.

These rows are tenant-scoped operational records, not organization
memberships or OpenFGA relations.  A support session must point at one active
row and every authorization check revalidates that row before allowing the
explicit resource/action scope.
"""
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    TIMESTAMP,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class PlatformAdminEligibility(Base):
    """Reviewed platform identity eligibility; never grants tenant access."""

    __tablename__ = "platform_admin_eligibilities"

    eligibility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    platform_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="active",
    )
    granted_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_by_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "platform_user_id",
            name="uq_platform_admin_eligibility_user",
        ),
        CheckConstraint(
            "role IN ('platform_support','platform_security_admin')",
            name="ck_platform_admin_eligibility_role",
        ),
        CheckConstraint(
            "status IN ('active','revoked','expired')",
            name="ck_platform_admin_eligibility_status",
        ),
        CheckConstraint(
            "expires_at > reviewed_at",
            name="ck_platform_admin_eligibility_review_window",
        ),
        CheckConstraint(
            "platform_user_id IS DISTINCT FROM reviewed_by_user_id",
            name="ck_platform_admin_eligibility_independent_review",
        ),
        Index(
            "ix_platform_admin_eligibility_status_expiry",
            "status",
            "expires_at",
        ),
    )


class PrivilegedAccessRequest(Base):
    __tablename__ = "privileged_access_requests"

    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    operator_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    requested_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="SET NULL"),
    )
    resource_type: Mapped[str | None] = mapped_column(Text)
    resource_id: Mapped[str | None] = mapped_column(Text)
    allowed_actions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False,
    )
    requested_duration_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False,
    )
    # Justification and ticket references are private content, not searchable
    # directory metadata.  They use the existing per-organization envelope.
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_encryption_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="requested",
    )
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
    )
    activated_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.session_id", ondelete="SET NULL"),
    )
    active_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
    )
    revoked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
    )
    request_expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested','approved','active','denied','revoked','expired')",
            name="ck_privileged_access_requests_status",
        ),
        CheckConstraint(
            "requested_duration_seconds BETWEEN 60 AND 1800",
            name="ck_privileged_access_requests_duration",
        ),
        CheckConstraint(
            "cardinality(allowed_actions) BETWEEN 1 AND 16",
            name="ck_privileged_access_requests_actions",
        ),
        CheckConstraint(
            "((resource_type IS NULL) = (resource_id IS NULL))",
            name="ck_privileged_access_requests_resource_pair",
        ),
        CheckConstraint(
            "operator_user_id IS DISTINCT FROM approved_by_user_id",
            name="ck_privileged_access_requests_two_person",
        ),
        Index(
            "ix_privileged_access_operator_status",
            "operator_user_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_privileged_access_tenant_expiry",
            "tenant_id",
            "status",
            "active_expires_at",
        ),
    )
