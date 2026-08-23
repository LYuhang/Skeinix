"""CRUD for API Management Center ``llm_credentials`` rows.

Mirrors ``repo_mcp_servers.py``. All methods assume the session is already
tenant-bound through ``session_scope(tenant_id=...)`` / ``tenant_db``
— they do NOT set the ``app.tenant_id`` GUC themselves. FORCE RLS + the
``deleted_at IS NULL`` clause on every SELECT make cross-tenant / soft-deleted
rows invisible.

The public "delete" path is a soft delete: an UPDATE that sets
``deleted_at = now()`` AND ``enabled = FALSE`` (so any future enabled-only
consumer — the PromptNode / agent picker phase — stops yielding it immediately).

Returns ``mappings()`` row dicts, NOT ORM ``LlmCredential`` instances (same
shape contract as ``McpServersRepo``).
"""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class LlmCredentialsRepo:
    """Async CRUD over ``llm_credentials``.

    Caller owns the transaction: mutating methods do NOT ``commit()`` (the DI
    request session commits at request end).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, credential_id: uuid.UUID) -> Optional[dict]:
        """Tenant-scoped fetch (session must already have ``app.tenant_id``
        set, or RLS hides the row). Filters soft-deleted rows."""
        row = (await self.session.execute(
            text(
                "SELECT * FROM llm_credentials "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": credential_id},
        )).mappings().one_or_none()
        return dict(row) if row else None

    async def get_for_user(
        self,
        credential_id: uuid.UUID,
        user_id: str | uuid.UUID,
    ) -> Optional[dict]:
        row = (
            await self.session.execute(
                text(
                    "SELECT * FROM llm_credentials "
                    "WHERE id = :id AND user_id = :user_id "
                    "AND deleted_at IS NULL"
                ),
                {"id": credential_id, "user_id": user_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row else None

    async def list_for_tenant(self) -> list[dict]:
        """List every live credential visible under the current tenant
        (RLS-scoped). Newest-first by ``created_at``."""
        rows = (await self.session.execute(
            text(
                "SELECT * FROM llm_credentials "
                "WHERE deleted_at IS NULL "
                "ORDER BY created_at DESC"
            ),
        )).mappings().all()
        return [dict(r) for r in rows]

    async def list_for_user(
        self,
        user_id: str | uuid.UUID,
    ) -> list[dict]:
        rows = (
            await self.session.execute(
                text(
                    "SELECT * FROM llm_credentials "
                    "WHERE user_id = :user_id AND deleted_at IS NULL "
                    "ORDER BY created_at DESC"
                ),
                {"user_id": user_id},
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    async def get_openrouter_for_user(
        self,
        user_id: str | uuid.UUID,
    ) -> Optional[dict]:
        row = (
            await self.session.execute(
                text(
                    "SELECT * FROM llm_credentials "
                    "WHERE user_id = :user_id "
                    "AND connection_kind = 'openrouter_oauth' "
                    "AND deleted_at IS NULL LIMIT 1"
                ),
                {"user_id": user_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row else None

    async def list_authorized(
        self,
        authorized_ids: tuple[str, ...] | list[str] | set[str],
    ) -> list[dict]:
        ids = tuple(dict.fromkeys(str(item) for item in authorized_ids if item))
        if not ids:
            return []
        rows = (
            await self.session.execute(
                text(
                    "SELECT * FROM llm_credentials "
                    "WHERE id = ANY(CAST(:ids AS uuid[])) "
                    "AND deleted_at IS NULL ORDER BY created_at DESC"
                ),
                {"ids": list(ids)},
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    async def insert(self, **fields) -> uuid.UUID:
        """Generic INSERT with caller-supplied columns. Defaults an ``id``
        UUID if absent. Returns the new id."""
        fields.setdefault("id", uuid.uuid4())
        cols = ", ".join(fields.keys())
        placeholders = ", ".join(f":{k}" for k in fields.keys())
        await self.session.execute(
            text(
                f"INSERT INTO llm_credentials ({cols}) VALUES ({placeholders})"
            ),
            fields,
        )
        return fields["id"]

    async def update(self, credential_id: uuid.UUID, **fields) -> None:
        """Patch named columns. Always bumps ``updated_at``. Ignored on
        soft-deleted rows. No-op when ``fields`` is empty."""
        if not fields:
            return
        sets = ", ".join(f"{k} = :{k}" for k in fields.keys())
        params = {"id": credential_id, **fields}
        await self.session.execute(
            text(
                f"UPDATE llm_credentials SET {sets}, updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            params,
        )

    async def soft_delete(self, credential_id: uuid.UUID) -> None:
        """Soft delete: mark ``deleted_at`` and disable. Idempotent —
        already-deleted rows are a no-op (the WHERE filters them).

        Flipping ``enabled = FALSE`` mirrors ``McpServersRepo.soft_delete`` so
        any future enabled-only scan stops yielding this row even before a
        ``deleted_at`` filter is added there."""
        await self.session.execute(
            text(
                "UPDATE llm_credentials "
                "SET deleted_at = now(), enabled = FALSE, updated_at = now() "
                "WHERE id = :id AND deleted_at IS NULL"
            ),
            {"id": credential_id},
        )
