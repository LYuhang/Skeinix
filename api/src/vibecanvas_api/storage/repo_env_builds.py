"""CRUD for the GLOBAL, content-addressed ``env_builds`` overlay registry.

``env_builds`` is tenant-agnostic and has NO RLS (see models_env_builds.py): an
overlay is shared public-PyPI content keyed by a sha256 of the declared
requirements. So this repo runs on a NON-tenant (admin) session — NOT a
``session_scope(tenant_id=...)`` tenant-bound one. Callers in production open
the admin session themselves:

    from vibecanvas_api.services.tenant_db import session_scope_admin
    async with session_scope_admin() as s:
        await EnvBuildsRepo(s).upsert_building(key, reqs)

The repo does NOT set any ``app.tenant_id`` GUC and does NOT ``commit()`` — the
caller owns the transaction (mirrors ``SkillsRepo`` / ``DeploymentsRepo``).
Returns ``mappings()`` row dicts, NOT ORM instances.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class EnvBuildsRepo:
    """Async CRUD over the global ``env_builds`` table. Caller owns the
    transaction — mutating methods do NOT ``commit()``."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, overlay_key: str) -> Optional[dict]:
        """Fetch a build row by its content key, or ``None`` if absent."""
        row = (await self.session.execute(
            text("SELECT * FROM env_builds WHERE overlay_key = :k"),
            {"k": overlay_key},
        )).mappings().one_or_none()
        return dict(row) if row else None

    async def upsert_building(self, overlay_key: str, requirements: str) -> None:
        """Insert a new ``building`` row, or — if the key already exists (e.g. a
        prior ``failed``/``ready`` build being rebuilt) — reset it back to
        ``building`` with the (possibly refreshed) requirements and clear the
        prior error / built_at. Idempotent: never creates a duplicate row."""
        await self.session.execute(
            text(
                "INSERT INTO env_builds (overlay_key, status, requirements) "
                "VALUES (:k, 'building', :r) "
                "ON CONFLICT (overlay_key) DO UPDATE SET "
                "status = 'building', requirements = EXCLUDED.requirements, "
                "error_log = NULL, built_at = NULL"
            ),
            {"k": overlay_key, "r": requirements},
        )

    async def mark_ready(self, overlay_key: str) -> None:
        """Mark a build done: status=ready, built_at=now, error cleared."""
        await self.session.execute(
            text(
                "UPDATE env_builds SET status = 'ready', built_at = now(), "
                "error_log = NULL WHERE overlay_key = :k"
            ),
            {"k": overlay_key},
        )

    async def mark_failed(self, overlay_key: str, error_log: str) -> None:
        """Mark a build failed: status=failed, record the pip stderr tail."""
        await self.session.execute(
            text(
                "UPDATE env_builds SET status = 'failed', error_log = :e "
                "WHERE overlay_key = :k"
            ),
            {"k": overlay_key, "e": error_log},
        )
