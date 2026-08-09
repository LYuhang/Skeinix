"""Durable, phase-separated account erasure worker.

Postgres owns the state machine.  Each external store is an independent phase,
so a crash resumes from the last committed boundary and a job is never marked
completed while a required phase is missing.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import os
from pathlib import Path
import shutil
from typing import Awaitable, Callable
import uuid

from sqlalchemy import delete, select, text, update

from vibecanvas_api.audit import actions
from vibecanvas_api.audit.service import record_auth_audit
from vibecanvas_api.config import config
from vibecanvas_api.security.redaction import redact_text
from vibecanvas_api.services.agent_runtime.checkpoint_store import LangChainCheckpointStore
from vibecanvas_api.services.object_store import get_object_store
from vibecanvas_api.services.sandbox.manager import get_existing_sandbox_manager
from vibecanvas_api.services.tenant_db import session_scope_admin
from vibecanvas_api.storage.models import Base, Chat
from vibecanvas_api.storage.models_purge import DataPurgeJob


LEASE_SECONDS = 15 * 60
PHASES = (
    "runtime_state",
    "object_store",
    "redis",
    "database",
    "identity",
    "backup_retention",
)


@dataclass(frozen=True, slots=True)
class PurgeLease:
    job_id: uuid.UUID
    deletion_request_id: uuid.UUID
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    completed_phases: tuple[str, ...]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def claim_due_purge_job() -> PurgeLease | None:
    """Claim one due job. Failed jobs require an explicit operator requeue."""
    async with session_scope_admin() as session:
        row = (
            await session.execute(
                select(DataPurgeJob)
                .where(
                    DataPurgeJob.status.in_(("queued", "running")),
                    DataPurgeJob.available_at <= _now(),
                    (
                        (DataPurgeJob.status == "queued")
                        | (DataPurgeJob.lease_expires_at < _now())
                    ),
                )
                .order_by(DataPurgeJob.available_at, DataPurgeJob.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        request_status = (
            await session.execute(
                text("SELECT status FROM account_deletion_requests WHERE id = :id"),
                {"id": row.deletion_request_id},
            )
        ).scalar_one_or_none()
        if request_status not in {"pending", "purging"}:
            row.status = "cancelled"
            row.lease_expires_at = None
            row.updated_at = _now()
            return None
        row.status = "running"
        row.current_phase = None
        row.attempt_count += 1
        row.started_at = row.started_at or _now()
        row.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
        row.updated_at = _now()
        await session.execute(
            text(
                "UPDATE account_deletion_requests SET status = 'purging', "
                "purging_at = coalesce(purging_at, now()), "
                "attempt_count = attempt_count + 1 WHERE id = :id"
            ),
            {"id": row.deletion_request_id},
        )
        await session.flush()
        return PurgeLease(
            job_id=row.job_id,
            deletion_request_id=row.deletion_request_id,
            user_id=row.user_id,
            tenant_id=row.tenant_id,
            completed_phases=tuple(row.completed_phases or []),
        )


async def _chat_runtime_coordinates(lease: PurgeLease) -> tuple[list[str], list[str]]:
    async with session_scope_admin() as session:
        rows = (
            await session.execute(
                select(Chat.chat_id, Chat.scope_id, Chat.major_version).where(
                    Chat.tenant_id == lease.tenant_id
                )
            )
        ).all()
    chats: list[str] = []
    threads: list[str] = []
    for chat_id, scope_id, major_version in rows:
        chats.append(str(chat_id))
        threads.append(
            f"{lease.user_id}__{scope_id}__v{major_version}__{chat_id}"
            if major_version
            else f"{lease.user_id}__{scope_id}__{chat_id}"
        )
    return chats, threads


def _safe_remove_tenant_directory(root: str, tenant_id: uuid.UUID) -> None:
    if not root:
        return
    resolved_root = Path(root).resolve()
    target = (resolved_root / str(tenant_id)).resolve()
    if target.parent != resolved_root:
        raise RuntimeError("purge directory escaped its configured root")
    if target.exists():
        shutil.rmtree(target)


async def _purge_runtime_state(lease: PurgeLease) -> None:
    manager = get_existing_sandbox_manager()
    if manager is not None:
        await manager.close_tenant(str(lease.tenant_id), reason="account_purge")
    chats, threads = await _chat_runtime_coordinates(lease)
    store = LangChainCheckpointStore()
    try:
        await store.purge_organization(
            str(lease.tenant_id), legacy_thread_ids=threads, chat_ids=chats
        )
    finally:
        await store.close()
    await asyncio.to_thread(
        _safe_remove_tenant_directory, config.agent_overlay_root, lease.tenant_id
    )
    await asyncio.to_thread(
        _safe_remove_tenant_directory, config.agent_runtime_root, lease.tenant_id
    )
    if os.path.realpath(config.vfs_volume_root) != os.path.realpath(
        config.agent_runtime_root
    ):
        await asyncio.to_thread(
            _safe_remove_tenant_directory, config.vfs_volume_root, lease.tenant_id
        )


async def _purge_object_store(lease: PurgeLease) -> None:
    store = get_object_store()
    tenant = str(lease.tenant_id)
    # Every prefix is non-empty and includes the exact organization UUID.
    prefixes = (
        f"artifacts/{tenant}/",
        f"run/{tenant}/",
        f"kb/{tenant}/",
        f"tasks/{tenant}/",
        f"batch/{tenant}/",
        f"skills/{tenant}/",
    )
    for prefix in prefixes:
        await asyncio.to_thread(store.delete_prefix, prefix)


async def _purge_redis(lease: PurgeLease) -> None:
    if not config.redis.url:
        return
    import redis.asyncio as aioredis

    client = aioredis.from_url(config.redis.url, decode_responses=False)
    tenant = str(lease.tenant_id)
    user = str(lease.user_id)
    patterns = (
        f"vibecanvas:*:organization:{tenant}:*",
        f"vibecanvas:*:tenant:{tenant}:*",
        f"vibecanvas:*:{tenant}:*",
        f"vibecanvas:auth:*:{user}:*",
    )
    try:
        for pattern in patterns:
            batch: list[bytes] = []
            async for key in client.scan_iter(match=pattern, count=250):
                batch.append(key)
                if len(batch) >= 250:
                    await client.delete(*batch)
                    batch.clear()
            if batch:
                await client.delete(*batch)
    finally:
        await client.aclose()


_PRESERVED_TENANT_TABLES = frozenset({
    "account_deletion_requests",
    "audit_log",
    "data_purge_jobs",
    "organizations",
    "tenants",
    "users",
})


async def _purge_database(lease: PurgeLease) -> None:
    async with session_scope_admin() as session:
        # Reverse FK order makes child rows disappear before their parents.
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _PRESERVED_TENANT_TABLES or "tenant_id" not in table.c:
                continue
            await session.execute(
                delete(table).where(table.c.tenant_id == lease.tenant_id)
            )
        # Membership in other organizations is user data, not owned content.
        await session.execute(
            text("DELETE FROM group_memberships WHERE user_id = :user_id"),
            {"user_id": lease.user_id},
        )
        await session.execute(
            text("DELETE FROM org_memberships WHERE user_id = :user_id"),
            {"user_id": lease.user_id},
        )


async def _purge_identity(lease: PurgeLease) -> None:
    marker = hashlib.sha256(str(lease.user_id).encode("ascii")).hexdigest()[:24]
    async with session_scope_admin() as session:
        await session.execute(
            text("DELETE FROM sessions WHERE user_id = :user_id"),
            {"user_id": lease.user_id},
        )
        await session.execute(
            text("DELETE FROM password_reset_tokens WHERE user_id = :user_id"),
            {"user_id": lease.user_id},
        )
        await session.execute(
            text("DELETE FROM auth_identities WHERE user_id = :user_id"),
            {"user_id": lease.user_id},
        )
        await session.execute(
            text(
                "UPDATE users SET profile_ciphertext=NULL, profile_nonce=NULL, "
                "profile_key_id=NULL, status='disabled', updated_at=now() "
                "WHERE user_id=:user_id"
            ),
            {"user_id": lease.user_id},
        )
        await session.execute(
            text("UPDATE tenants SET name = :name WHERE tenant_id = :tenant_id"),
            {"name": f"deleted-{marker}", "tenant_id": lease.tenant_id},
        )
        await session.execute(
            text(
                "UPDATE organizations SET name = :name, slug = :slug, updated_at = now() "
                "WHERE tenant_id = :tenant_id"
            ),
            {
                "name": "Deleted account",
                "slug": f"deleted-{marker}",
                "tenant_id": lease.tenant_id,
            },
        )
        await session.execute(
            text(
                "UPDATE account_deletion_requests SET "
                "email_snapshot_ciphertext=NULL, email_snapshot_nonce=NULL, "
                "email_snapshot_key_id=NULL WHERE id=:request_id"
            ),
            {"request_id": lease.deletion_request_id},
        )
        await session.execute(
            text(
                "DELETE FROM content_encryption_keys "
                "WHERE tenant_id=:tenant_id AND resource_type='user_identity' "
                "AND resource_id=:resource_id"
            ),
            {
                "tenant_id": lease.tenant_id,
                "resource_id": str(lease.user_id),
            },
        )


async def _record_backup_retention(_lease: PurgeLease) -> None:
    # Active stores have already been erased. Immutable encrypted backups age
    # out under the deployment retention policy; production startup separately
    # requires BACKUP_ENCRYPTION_VERIFIED before this worker may run.
    return None


_PHASE_HANDLERS: dict[str, Callable[[PurgeLease], Awaitable[None]]] = {
    "runtime_state": _purge_runtime_state,
    "object_store": _purge_object_store,
    "redis": _purge_redis,
    "database": _purge_database,
    "identity": _purge_identity,
    "backup_retention": _record_backup_retention,
}


async def _mark_phase(job_id: uuid.UUID, phase: str) -> None:
    async with session_scope_admin() as session:
        row = (
            await session.execute(
                select(DataPurgeJob).where(DataPurgeJob.job_id == job_id).with_for_update()
            )
        ).scalar_one()
        completed = list(row.completed_phases or [])
        if phase not in completed:
            completed.append(phase)
        row.completed_phases = completed
        row.current_phase = None
        row.lease_expires_at = _now() + timedelta(seconds=LEASE_SECONDS)
        row.updated_at = _now()


async def _finalize(lease: PurgeLease) -> None:
    async with session_scope_admin() as session:
        row = (
            await session.execute(
                select(DataPurgeJob).where(DataPurgeJob.job_id == lease.job_id).with_for_update()
            )
        ).scalar_one()
        missing = [phase for phase in PHASES if phase not in (row.completed_phases or [])]
        if missing:
            raise RuntimeError("purge phases are incomplete")
        row.status = "completed"
        row.current_phase = None
        row.lease_expires_at = None
        row.completed_at = _now()
        row.updated_at = _now()
        await session.execute(
            text(
                "UPDATE account_deletion_requests SET status = 'purged', "
                "purged_at = now(), last_error = NULL WHERE id = :id"
            ),
            {"id": lease.deletion_request_id},
        )


async def _fail(lease: PurgeLease, phase: str, exc: Exception) -> None:
    message = redact_text(str(exc))[:1000] or "purge phase failed"
    async with session_scope_admin() as session:
        await session.execute(
            update(DataPurgeJob)
            .where(DataPurgeJob.job_id == lease.job_id)
            .values(
                status="failed",
                current_phase=phase,
                lease_expires_at=None,
                last_error_code=type(exc).__name__[:128],
                last_error_message=message,
                updated_at=_now(),
            )
        )
        await session.execute(
            text(
                "UPDATE account_deletion_requests SET status = 'failed', "
                "last_error = :error WHERE id = :id"
            ),
            {"error": message, "id": lease.deletion_request_id},
        )


async def run_purge_job(lease: PurgeLease) -> None:
    await record_auth_audit(
        action=actions.PURGE_STARTED,
        actor_user_id=lease.user_id,
        actor_email=None,
        tenant_id=lease.tenant_id,
        outcome="success",
        meta={"job_id": str(lease.job_id)},
    )
    completed = set(lease.completed_phases)
    current = "claim"
    try:
        for phase in PHASES:
            if phase in completed:
                continue
            current = phase
            async with session_scope_admin() as session:
                await session.execute(
                    update(DataPurgeJob)
                    .where(DataPurgeJob.job_id == lease.job_id)
                    .values(current_phase=phase, updated_at=_now())
                )
            await _PHASE_HANDLERS[phase](lease)
            await _mark_phase(lease.job_id, phase)
        await _finalize(lease)
        await record_auth_audit(
            action=actions.PURGE_COMPLETED,
            actor_user_id=lease.user_id,
            actor_email=None,
            tenant_id=lease.tenant_id,
            outcome="success",
            meta={"job_id": str(lease.job_id)},
        )
    except Exception as exc:
        await _fail(lease, current, exc)
        await record_auth_audit(
            action=actions.PURGE_FAILED,
            actor_user_id=lease.user_id,
            actor_email=None,
            tenant_id=lease.tenant_id,
            outcome="failure",
            meta={"job_id": str(lease.job_id), "phase": current},
        )
        raise


async def run_one_due_purge() -> bool:
    lease = await claim_due_purge_job()
    if lease is None:
        return False
    await run_purge_job(lease)
    return True
