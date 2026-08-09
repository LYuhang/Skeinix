"""MCP servers — per-tenant configurations for external MCP tool providers.

Spec §4. FORCE RLS + soft delete + partial unique indexes.

This table registers on the shared ``Base.metadata`` declared in
``storage/models.py``; migration 001's ``Base.metadata.create_all(bind)``
reflects the current models so it creates the table alongside the
application and deployment tables. Migration 006 adds the RLS policy
and partial indexes that ``create_all`` cannot express.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, CheckConstraint, DateTime, ForeignKey, Integer, Text, UniqueConstraint,
    func, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    tool_prefix: Mapped[str] = mapped_column(Text, nullable=False)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    auth_mode: Mapped[str] = mapped_column(
        Text, nullable=False, default="none", server_default="none",
    )
    auth_metadata_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    connection_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="not_required", server_default="not_required",
    )
    description: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default="",
    )
    description_source: Mapped[str] = mapped_column(
        Text, nullable=False, default="fallback", server_default="fallback",
    )
    description_model_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    description_generated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    description_basis_hash: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    auth_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    auth_secret_ref: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("encrypted_secrets.secret_id", ondelete="RESTRICT"),
    )
    auth_secret_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    connection_config: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"),
    )
    connection_secret_ref: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("encrypted_secrets.secret_id", ondelete="RESTRICT"),
    )
    connection_secret_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    last_handshake_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_handshake_status: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True,
    )
    last_tool_count: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True,
    )
    last_tool_names: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "transport IN ('stdio', 'sse', 'streamable_http', 'streamable-http', 'http')",
            name="ck_mcp_servers_transport",
        ),
        CheckConstraint(
            "tool_prefix ~ '^[a-z][a-z0-9_]{0,30}$'",
            name="ck_mcp_servers_prefix_format",
        ),
        CheckConstraint(
            "description_source IN ('registry', 'server_metadata', 'synthesized', 'user_edited', 'ai_generated', 'fallback')",
            name="ck_mcp_servers_description_source",
        ),
        CheckConstraint(
            "auth_mode IN ('none', 'configuration', 'connection_discovery', 'oauth')",
            name="ck_mcp_servers_auth_mode",
        ),
        CheckConstraint(
            "connection_status IN ('not_required', 'connection_required', 'connecting', 'connected', 'reconnect_required', 'connection_failed')",
            name="ck_mcp_servers_connection_status",
        ),
        CheckConstraint(
            "NOT (auth_config ? 'token') AND "
            "((auth_config->>'type' != 'bearer') OR "
            "auth_secret_ref IS NOT NULL)",
            name="ck_mcp_auth_secret_reference",
        ),
        CheckConstraint(
            "position('?' in endpoint)=0 AND "
            "position('?' in coalesce(connection_config->>'url',''))=0 AND "
            "coalesce(connection_config->'headers','{}'::jsonb)='{}'::jsonb "
            "AND coalesce(connection_config->'env','{}'::jsonb)='{}'::jsonb",
            name="ck_mcp_connection_public_projection",
        ),
        # Partial UNIQUE indexes on (tenant_id, name) and (tenant_id, tool_prefix)
        # WHERE deleted_at IS NULL plus a regular partial index on
        # (tenant_id, enabled) live in migration 006 — SQLAlchemy's
        # __table_args__ cannot express partial indexes portably without
        # coupling to the column object before it exists.
    )


class McpOAuthConnection(Base):
    __tablename__ = "mcp_oauth_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False,
    )
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False,
    )
    authorization_server: Mapped[str] = mapped_column(Text, nullable=False)
    token_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    revocation_endpoint: Mapped[Optional[str]] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    # The complete OAuth token bundle exists only in SecretService.
    secret_ref: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("encrypted_secrets.secret_id", ondelete="RESTRICT"),
        nullable=False,
    )
    secret_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    token_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="Bearer")
    scope: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (UniqueConstraint("server_id", name="uq_mcp_oauth_connections_server"),)


class McpOAuthTransaction(Base):
    __tablename__ = "mcp_oauth_transactions"

    state_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False,
    )
    server_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mcp_servers.id", ondelete="CASCADE"), nullable=False,
    )
    # PKCE verifier and optional dynamic-registration client secret are one
    # short-lived SecretService bundle.
    secret_ref: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("encrypted_secrets.secret_id", ondelete="RESTRICT"),
        nullable=False,
    )
    secret_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)
    return_origin: Mapped[str] = mapped_column(Text, nullable=False)
    authorization_server: Mapped[str] = mapped_column(Text, nullable=False)
    token_endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    revocation_endpoint: Mapped[Optional[str]] = mapped_column(Text)
    resource: Mapped[str] = mapped_column(Text, nullable=False)
    client_id: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[Optional[str]] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
