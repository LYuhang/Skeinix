"""Per-resource envelope keys for durable user content."""
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class ContentEncryptionKey(Base):
    __tablename__ = "content_encryption_keys"

    key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    algorithm: Mapped[str] = mapped_column(
        Text, nullable=False, default="AES-256-GCM"
    )
    wrapped_dek: Mapped[str] = mapped_column(Text, nullable=False)
    wrapping_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    wrapping_key_version: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_content_keys_version"),
        CheckConstraint(
            "status IN ('active','retired','destroyed')",
            name="ck_content_keys_status",
        ),
        CheckConstraint(
            "algorithm = 'AES-256-GCM'",
            name="ck_content_keys_algorithm",
        ),
        UniqueConstraint(
            "tenant_id",
            "resource_type",
            "resource_id",
            "version",
            name="uq_content_keys_resource_version",
        ),
        Index(
            "ix_content_keys_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
            "status",
        ),
    )
