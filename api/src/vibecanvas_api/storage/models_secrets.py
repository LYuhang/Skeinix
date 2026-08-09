"""Encrypted secret records owned by the host-side SecretService."""
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class EncryptedSecret(Base):
    __tablename__ = "encrypted_secrets"

    secret_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="active")
    algorithm: Mapped[str] = mapped_column(
        Text, nullable=False, default="AES-256-GCM"
    )
    ciphertext: Mapped[str | None] = mapped_column(Text)
    nonce: Mapped[str | None] = mapped_column(Text)
    wrapped_dek: Mapped[str | None] = mapped_column(Text)
    wrapping_key_id: Mapped[str] = mapped_column(Text, nullable=False)
    wrapping_key_version: Mapped[str] = mapped_column(Text, nullable=False)
    context_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    destroyed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        CheckConstraint("version > 0", name="ck_encrypted_secrets_version"),
        CheckConstraint(
            "status IN ('active','superseded','destroyed')",
            name="ck_encrypted_secrets_status",
        ),
        CheckConstraint(
            "algorithm = 'AES-256-GCM'",
            name="ck_encrypted_secrets_algorithm",
        ),
        UniqueConstraint(
            "tenant_id",
            "purpose",
            "resource_type",
            "resource_id",
            "version",
            name="uq_encrypted_secrets_resource_version",
        ),
        Index(
            "ix_encrypted_secrets_resource",
            "tenant_id",
            "resource_type",
            "resource_id",
            "purpose",
        ),
    )
