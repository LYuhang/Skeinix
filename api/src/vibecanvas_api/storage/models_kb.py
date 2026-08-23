"""KB / RAG SQLAlchemy ORM models — spec sec 4.2-4.4.

Three tables: knowledge_bases / kb_files / kb_chunks. All tenant-scoped
via FORCE RLS (policies created in alembic 007). Each row carries a
``tenant_id`` UUID that Postgres' ``tenant_isolation`` policy compares
against the per-transaction ``app.tenant_id`` GUC.

Soft-delete pattern: ``knowledge_bases`` + ``kb_files`` use
``deleted_at`` (UPDATE on delete; the GC sweeper in T11 issues real
DELETEs after 30 days, and ``ON DELETE CASCADE`` only fires then).
``kb_chunks`` are hard-deleted by the indexer when a file is re-indexed,
so they have no ``deleted_at``.

This module registers its ORM classes on the shared ``Base.metadata``
declared in ``storage/models.py``; migration 001's
``Base.metadata.create_all(bind)`` reflects the current models, so the
tables are created alongside the application, deployment, and MCP
tables. Migration 007 adds what ``create_all`` cannot express (RLS, HNSW
+ GIN indexes, partial functional index on ``tasks``).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Index,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    __allow_unmapped__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False)
    name_lookup_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_encryption_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: str = ""
    description: str | None = None
    summary: str | None = None
    # Monotonic package revision. The raw file tree is authoritative; search
    # chunks are replaceable projections of the files at this revision.
    package_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index(
            "uq_kb_tenant_name_active", "tenant_id", "name_lookup_hash",
            unique=True, postgresql_where="deleted_at IS NULL",
        ),
    )


class KbFile(Base):
    __tablename__ = "kb_files"
    __allow_unmapped__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id", ondelete="RESTRICT"),
        nullable=False)
    private_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    private_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_encryption_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: str = ""
    error_message: str | None = None
    parser_type: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    object_store_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending")
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('stored','pending','indexing','indexed','failed')",
            name="ck_kb_files_status"),
        Index("ix_kb_files_kb_deleted", "kb_id", "deleted_at"),
        Index(
            "ix_kb_files_status_orphan", "status", "updated_at",
            postgresql_where="status IN ('pending','indexing')"),
    )


class KbChunk(Base):
    __tablename__ = "kb_chunks"
    __allow_unmapped__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("kb_files.id", ondelete="CASCADE"),
        nullable=False)
    kb_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content_ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    content_nonce: Mapped[str] = mapped_column(Text, nullable=False)
    content_key_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("content_encryption_keys.key_id", ondelete="RESTRICT"),
        nullable=False,
    )
    text: str = ""
    chunk_metadata: dict | None = None
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_kb_chunks_kb_id", "kb_id"),
    )
