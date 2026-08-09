"""Enterprise OIDC/SCIM control-plane persistence.

These are identity-system records, so provider/token lookup must work before a
tenant is known. Application routes still bind and validate the provider's
tenant before touching organization, group, or membership rows.
"""
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Text,
    TIMESTAMP,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class EnterpriseIdentityProvider(Base):
    __tablename__ = "enterprise_identity_providers"

    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    organization_slug: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    issuer_url: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    token_endpoint_auth_method: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default="client_secret_basic",
    )
    client_secret_ref: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("encrypted_secrets.secret_id", ondelete="SET NULL"),
    )
    subject_claim: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="sub",
    )
    email_claim: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="email",
    )
    display_name_claim: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="name",
    )
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{openid,profile,email}",
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active",
    )
    scim_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scim_token_generation: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default="1",
    )
    scim_token_expires_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )
    last_scim_sync_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('active','disabled')",
            name="ck_enterprise_identity_provider_status",
        ),
        CheckConstraint(
            "token_endpoint_auth_method IN "
            "('client_secret_basic','client_secret_post','none')",
            name="ck_enterprise_identity_provider_token_auth_method",
        ),
        CheckConstraint(
            "scim_token_generation > 0",
            name="ck_enterprise_identity_provider_token_generation",
        ),
        Index(
            "uq_enterprise_identity_provider_tenant_issuer",
            "tenant_id",
            "issuer_url",
            unique=True,
        ),
        Index(
            "ix_enterprise_identity_provider_tenant",
            "tenant_id",
            "status",
        ),
        Index(
            "ix_enterprise_identity_provider_organization_slug",
            "organization_slug",
            "status",
        ),
    )


class EnterpriseDirectoryUser(Base):
    __tablename__ = "enterprise_directory_users"

    directory_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
        server_default=func.gen_random_uuid(),
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enterprise_identity_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id_lookup_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_name_lookup_hash: Mapped[str] = mapped_column(Text, nullable=False)
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_encryption_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index(
            "uq_enterprise_directory_user_external",
            "provider_id",
            "external_id_lookup_hash",
            unique=True,
        ),
        Index(
            "uq_enterprise_directory_user_name",
            "provider_id",
            "user_name_lookup_hash",
            unique=True,
        ),
        Index(
            "uq_enterprise_directory_user_local",
            "provider_id",
            "user_id",
            unique=True,
        ),
        Index(
            "ix_enterprise_directory_user_tenant",
            "tenant_id",
            "active",
        ),
    )


class OidcLoginTransaction(Base):
    __tablename__ = "oidc_login_transactions"

    state_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("enterprise_identity_providers.provider_id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    secret_ref: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("encrypted_secrets.secret_id", ondelete="CASCADE"),
        nullable=False,
    )
    return_to: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        Index("ix_oidc_login_transaction_expiry", "expires_at"),
    )


__all__ = [
    "EnterpriseDirectoryUser",
    "EnterpriseIdentityProvider",
    "OidcLoginTransaction",
]
