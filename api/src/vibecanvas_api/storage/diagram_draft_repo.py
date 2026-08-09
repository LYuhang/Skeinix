"""Durable Ready Revision repository for live Diagram Preview."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.storage.models import DiagramDraft, DiagramRenderRevision
from vibecanvas_api.storage.vfs_store import VfsRepo

READY_REVISION_RETENTION = 100


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DraftSourceCursor:
    draft_id: str
    sequence: int


class DiagramDraftRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def begin_source(
        self,
        *,
        tenant_id: str,
        owner_user_id: str,
        chat_id: str,
        turn_id: str,
        workspace_scope_id: str,
        source_path: str,
        target_path: str,
        source_hash: str,
    ) -> DraftSourceCursor:
        """Allocate one monotonically increasing source sequence."""
        candidate_id = uuid.uuid4()
        await self._session.execute(
            pg_insert(DiagramDraft)
            .values(
                draft_id=candidate_id,
                tenant_id=uuid.UUID(tenant_id),
                owner_user_id=uuid.UUID(owner_user_id),
                chat_id=chat_id,
                turn_id=turn_id,
                workspace_scope_id=workspace_scope_id,
                source_path=source_path,
                target_path=target_path,
                status="parsing",
            )
            .on_conflict_do_nothing(
                constraint="uq_diagram_drafts_turn_source"
            )
        )
        draft = (
            await self._session.execute(
                select(DiagramDraft)
                .where(
                    DiagramDraft.chat_id == chat_id,
                    DiagramDraft.turn_id == turn_id,
                    DiagramDraft.source_path == source_path,
                )
                .with_for_update()
            )
        ).scalar_one()
        if draft.terminal:
            # A terminal draft is immutable. A new Turn naturally creates a new
            # key; receiving another write for this exact Turn is a stale Agent
            # continuation and must not mutate the completed timeline.
            raise ValueError("diagram draft is already terminal")
        sequence = int(draft.latest_source_sequence) + 1
        draft.latest_source_sequence = sequence
        draft.status = "parsing"
        draft.updated_at = _now()
        await self._session.execute(
            pg_insert(DiagramRenderRevision).values(
                draft_id=draft.draft_id,
                sequence=sequence,
                tenant_id=uuid.UUID(tenant_id),
                status="parsing",
                source_hash=source_hash,
            )
        )
        await self._session.flush()
        return DraftSourceCursor(str(draft.draft_id), sequence)

    async def mark_compiling(self, draft_id: str, sequence: int) -> None:
        await self._set_revision_status(draft_id, sequence, "compiling")

    async def mark_invalid(self, draft_id: str, sequence: int) -> None:
        await self._set_revision_status(draft_id, sequence, "invalid")

    async def _set_revision_status(
        self,
        draft_id: str,
        sequence: int,
        status: str,
    ) -> None:
        key = uuid.UUID(draft_id)
        await self._session.execute(
            update(DiagramRenderRevision)
            .where(
                DiagramRenderRevision.draft_id == key,
                DiagramRenderRevision.sequence == sequence,
            )
            .values(status=status)
        )
        draft = await self._session.get(DiagramDraft, key, with_for_update=True)
        if draft is not None and sequence == int(draft.latest_source_sequence):
            draft.status = status
            draft.updated_at = _now()
        await self._session.flush()

    async def mark_ready(
        self,
        *,
        draft_id: str,
        sequence: int,
        tenant_id: str,
        workspace_scope_id: str,
        source_hash: str,
        scene_ref: str,
        scene_hash: str,
        scene_bytes: bytes,
        operation: str,
        element_ids: list[str],
    ) -> str:
        """Persist a trusted Scene, then publish its ready cursor atomically."""
        key = uuid.UUID(draft_id)
        scene_path = f"/__diagram_drafts/{draft_id}/{sequence}.scene.json"
        vfs = VfsRepo(self._session, object_store=get_object_store())
        await vfs.upsert_internal_artifact_bytes(
            wf_id=workspace_scope_id,
            tenant=tenant_id,
            path=scene_path,
            data=scene_bytes,
            content_type="application/vnd.vibecanvas.diagram-scene+json",
            abstract="Diagram draft ready revision",
        )
        draft = await self._session.get(DiagramDraft, key, with_for_update=True)
        if draft is None:
            raise ValueError("diagram draft not found")
        revision = await self._session.get(
            DiagramRenderRevision,
            (key, sequence),
            with_for_update=True,
        )
        if revision is None:
            raise ValueError("diagram source revision not found")
        revision.operation = operation
        revision.element_ids = sorted(set(element_ids))
        revision.scene_ref = scene_ref
        revision.scene_hash = scene_hash
        revision.scene_path = scene_path
        if sequence < int(draft.latest_source_sequence):
            revision.status = "superseded"
        else:
            revision.status = "ready"
            draft.latest_ready_sequence = sequence
            draft.latest_ready_scene_ref = scene_ref
            draft.status = "ready"
            draft.updated_at = _now()
        await self._trim_history(draft, vfs)
        await self._session.flush()
        return revision.status

    async def _trim_history(self, draft: DiagramDraft, vfs: VfsRepo) -> None:
        rows = (
            await self._session.execute(
                select(DiagramRenderRevision)
                .where(DiagramRenderRevision.draft_id == draft.draft_id)
                .order_by(DiagramRenderRevision.sequence.desc())
                .offset(READY_REVISION_RETENTION)
            )
        ).scalars().all()
        for row in rows:
            if row.scene_path:
                await vfs.delete_artifact(
                    wf_id=draft.workspace_scope_id,
                    tenant=str(draft.tenant_id),
                    path=row.scene_path,
                )
            await self._session.delete(row)

    async def mark_terminal(
        self,
        *,
        draft_id: str,
        sequence: int,
        status: str = "committed",
    ) -> None:
        if status not in {"committed", "cancelled"}:
            raise ValueError("invalid terminal Diagram draft status")
        key = uuid.UUID(draft_id)
        draft = await self._session.get(DiagramDraft, key, with_for_update=True)
        if draft is None:
            return
        if sequence != int(draft.latest_ready_sequence):
            raise ValueError("cannot commit a stale Diagram draft revision")
        draft.status = status
        draft.terminal = status == "cancelled"
        draft.updated_at = _now()
        await self._session.execute(
            update(DiagramRenderRevision)
            .where(
                DiagramRenderRevision.draft_id == key,
                DiagramRenderRevision.sequence == sequence,
            )
            .values(status=status)
        )
        await self._session.flush()

    async def finalize_latest(
        self,
        *,
        chat_id: str,
        turn_id: str,
        completed: bool,
    ) -> None:
        draft = (
            await self._session.execute(
                select(DiagramDraft)
                .where(
                    DiagramDraft.chat_id == chat_id,
                    DiagramDraft.turn_id == turn_id,
                )
                .order_by(DiagramDraft.updated_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if draft is None or int(draft.latest_ready_sequence) == 0:
            return
        final_status = (
            "committed" if completed and draft.status == "committed" else "cancelled"
        )
        draft.status = final_status
        draft.terminal = True
        draft.updated_at = _now()
        await self._session.execute(
            update(DiagramRenderRevision)
            .where(
                DiagramRenderRevision.draft_id == draft.draft_id,
                DiagramRenderRevision.sequence == draft.latest_ready_sequence,
            )
            .values(status=final_status)
        )
        await self._session.flush()

    async def get_owned(self, draft_id: str, owner_user_id: str) -> DiagramDraft | None:
        try:
            key = uuid.UUID(draft_id)
            owner = uuid.UUID(owner_user_id)
        except ValueError:
            return None
        return (
            await self._session.execute(
                select(DiagramDraft).where(
                    DiagramDraft.draft_id == key,
                    DiagramDraft.owner_user_id == owner,
                )
            )
        ).scalar_one_or_none()

    async def ready_revisions(
        self,
        draft_id: str,
        *,
        after: int,
        limit: int,
    ) -> tuple[list[DiagramRenderRevision], int | None]:
        key = uuid.UUID(draft_id)
        minimum = (
            await self._session.execute(
                select(DiagramRenderRevision.sequence)
                .where(
                    DiagramRenderRevision.draft_id == key,
                    DiagramRenderRevision.status.in_(("ready", "committed")),
                )
                .order_by(DiagramRenderRevision.sequence)
                .limit(1)
            )
        ).scalar_one_or_none()
        rows = (
            await self._session.execute(
                select(DiagramRenderRevision)
                .where(
                    DiagramRenderRevision.draft_id == key,
                    DiagramRenderRevision.status.in_(("ready", "committed")),
                    DiagramRenderRevision.sequence > after,
                )
                .order_by(DiagramRenderRevision.sequence)
                .limit(limit)
            )
        ).scalars().all()
        return list(rows), int(minimum) if minimum is not None else None
