"""Persistence boundary for delivering terminal background-job results."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .background_jobs_repo import BackgroundJobsRepo
from .models import Chat
from .models_background_jobs import (
    ACTIVE_BACKGROUND_JOB_STATUSES,
    TERMINAL_BACKGROUND_JOB_STATUSES,
    ChatToolJob,
    ChatToolJobDelivery,
)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_undelivered():
    return ~exists(
        select(ChatToolJobDelivery.job_id).where(
            ChatToolJobDelivery.job_id == ChatToolJob.job_id
        )
    )


class BackgroundDeliveryRepo:
    """Delivery ledger queries; never mutates background execution status."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_pending_terminal_for_user(
        self,
        *,
        chat_id: str,
        creator_user_id: str | uuid.UUID,
        limit: int = 100,
    ) -> list[ChatToolJob]:
        query = (
            select(ChatToolJob)
            .join(Chat, Chat.chat_id == ChatToolJob.chat_id)
            .where(
                ChatToolJob.chat_id == chat_id,
                Chat.creator_user_id == _uuid(creator_user_id),
                Chat.deleted_at.is_(None),
                ChatToolJob.status.in_(TERMINAL_BACKGROUND_JOB_STATUSES),
                _is_undelivered(),
            )
            .order_by(ChatToolJob.finished_at, ChatToolJob.created_at)
            .limit(max(1, min(int(limit), 100)))
        )
        rows = list((await self.session.execute(query)).scalars().all())
        jobs_repo = BackgroundJobsRepo(self.session)
        for row in rows:
            await jobs_repo._materialize_job(row)
        return rows

    async def claim_batch(
        self,
        *,
        chat_id: str,
        creator_user_id: str | uuid.UUID,
        job_ids: Iterable[str],
        delivery_batch_id: str,
    ) -> list[ChatToolJob]:
        """Insert the ledger entries atomically with the durable result Turn."""

        requested = tuple(dict.fromkeys(str(value) for value in job_ids))
        if not requested:
            return []
        rows = list(
            (
                await self.session.execute(
                    select(ChatToolJob)
                    .join(Chat, Chat.chat_id == ChatToolJob.chat_id)
                    .where(
                        ChatToolJob.chat_id == chat_id,
                        Chat.creator_user_id == _uuid(creator_user_id),
                        Chat.deleted_at.is_(None),
                        ChatToolJob.job_id.in_(requested),
                        ChatToolJob.status.in_(
                            TERMINAL_BACKGROUND_JOB_STATUSES
                        ),
                        _is_undelivered(),
                    )
                    .order_by(
                        ChatToolJob.finished_at,
                        ChatToolJob.created_at,
                    )
                    .with_for_update(of=ChatToolJob)
                )
            )
            .scalars()
            .all()
        )
        delivered_at = _now()
        jobs_repo = BackgroundJobsRepo(self.session)
        for row in rows:
            await jobs_repo._materialize_job(row)
            delivery = ChatToolJobDelivery(
                job_id=row.job_id,
                tenant_id=row.tenant_id,
                chat_id=row.chat_id,
                delivery_batch_id=delivery_batch_id,
                delivered_at=delivered_at,
            )
            self.session.add(delivery)
            row.delivery = delivery

            # Delivery is a separate state machine, but it still publishes onto
            # the shared durable UI event stream so View updates immediately.
            row.event_seq = int(row.event_seq or 0) + 1
            row.updated_at = delivered_at
            self.session.add(
                await jobs_repo._new_event(
                    row=row,
                    event_type="delivered",
                    payload={
                        "execution_status": row.status,
                        "delivery_status": "delivered",
                        "delivered_at": delivered_at.isoformat(),
                        "delivery_batch_id": delivery_batch_id,
                    },
                )
            )
        await self.session.flush()
        return rows

    async def has_sandbox_hold(self, chat_id: str) -> bool:
        row = (
            await self.session.execute(
                select(ChatToolJob.job_id)
                .where(
                    ChatToolJob.chat_id == chat_id,
                    or_(
                        ChatToolJob.status.in_(
                            ACTIVE_BACKGROUND_JOB_STATUSES
                        ),
                        (
                            ChatToolJob.status.in_(
                                TERMINAL_BACKGROUND_JOB_STATUSES
                            )
                            & _is_undelivered()
                        ),
                    ),
                )
                .limit(1)
            )
        ).first()
        return row is not None

    async def list_sandbox_holds_for_user(
        self,
        *,
        chat_id: str,
        creator_user_id: str | uuid.UUID,
        limit: int = 200,
    ) -> list[ChatToolJob]:
        query = (
            select(ChatToolJob)
            .join(Chat, Chat.chat_id == ChatToolJob.chat_id)
            .where(
                ChatToolJob.chat_id == chat_id,
                Chat.creator_user_id == _uuid(creator_user_id),
                Chat.deleted_at.is_(None),
                or_(
                    ChatToolJob.status.in_(ACTIVE_BACKGROUND_JOB_STATUSES),
                    (
                        ChatToolJob.status.in_(
                            TERMINAL_BACKGROUND_JOB_STATUSES
                        )
                        & _is_undelivered()
                    ),
                ),
            )
            .order_by(ChatToolJob.created_at)
            .limit(max(1, min(int(limit), 200)))
        )
        rows = list((await self.session.execute(query)).scalars().all())
        jobs_repo = BackgroundJobsRepo(self.session)
        for row in rows:
            await jobs_repo._materialize_job(row)
        return rows
