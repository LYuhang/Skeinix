"""env_builds — GLOBAL, content-addressed Python-library overlay build registry.

A "build" is one materialized pip overlay keyed by a sha256 hash of the
declared requirements (the ``overlay_key``). Because the overlay is nothing but
public-PyPI content, it is identical for every tenant that declares the same
requirements — so this table is **deliberately tenant-agnostic and has NO RLS**.
A tenant only ever looks a row up by its content-derived key; there is no
per-tenant row, no ``tenant_id``, and no ``tenant_isolation`` policy.

This is an INTENTIONAL exception to this repo's "every tenant table FORCE RLS"
convention. The build registry is a shared cache, not tenant data.

Registers on the shared ``Base.metadata`` (via the tail-import in
``storage/models.py``) so migration 001's ``Base.metadata.create_all`` picks it
up on a FRESH DB. Migration 026 also ``CREATE TABLE IF NOT EXISTS`` it so it
lands on an already-migrated/persistent DB (the create_all-only gap — see
migrations 021/022/025).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, String, Text, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class EnvBuild(Base):
    __tablename__ = "env_builds"

    # The sha256 hex of the declared requirements (64 chars) — the content key.
    # NO tenant_id, NO RLS: this is a deliberate global content-addressed cache.
    overlay_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_log: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    requirements: Mapped[str] = mapped_column(Text, nullable=False)
    built_at: Mapped[Optional[datetime]] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now(),
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('building', 'ready', 'failed')",
            name="ck_env_builds_status",
        ),
    )
