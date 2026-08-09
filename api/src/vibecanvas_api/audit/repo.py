"""Audit repository with inserts and tenant-scoped
cursor list (read API). The auth-path raw INSERT lives in audit/service.py."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy import select, text

from vibecanvas_api.security.audit_protection import (
    audit_lookup_digest,
    decrypt_audit_payload,
    encrypt_audit_payload,
)
from vibecanvas_api.storage.models import AuditLog


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: uuid.UUID
    tenant_id: uuid.UUID | None
    actor_user_id: uuid.UUID | None
    actor_email: str | None
    action: str
    target_type: str | None
    target_id: str | None
    target_name: str | None
    outcome: str
    ip_address: str | None
    user_agent: str | None
    request_id: str | None
    meta: dict
    created_at: datetime


class AuditRepo:
    def __init__(self, session) -> None:
        self.session = session

    async def add_row(self, *, action, actor_user_id, actor_email, target_type,
                      target_id, target_name, outcome, ip_address, user_agent,
                      request_id, meta) -> None:
        """Resource-path insert: ORM add into the caller's tenant session.
        tenant_id is OMITTED → the RLS server-default fills it from
        app.tenant_id (the desired behavior here). session.add is sync; the
        tenant_db teardown commits."""
        tenant_id = (
            await self.session.execute(
                text(
                    "SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid"
                )
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise RuntimeError("tenant audit write requires an active tenant")
        audit_id = uuid.uuid4()
        encrypted = await encrypt_audit_payload(
            self.session,
            audit_id=audit_id,
            tenant_id=tenant_id,
            actor_email=actor_email,
            target_name=target_name,
            ip_address=ip_address,
            user_agent=user_agent,
            meta=meta or {},
        )
        self.session.add(AuditLog(
            audit_id=audit_id,
            action=action,
            actor_user_id=actor_user_id,
            actor_email=None,
            actor_lookup_hash=audit_lookup_digest("actor_email", actor_email),
            target_type=target_type,
            target_id=target_id,
            target_name=None,
            outcome=outcome,
            ip_address=None,
            ip_lookup_hash=audit_lookup_digest("ip_address", ip_address),
            user_agent=None,
            request_id=request_id,
            meta={},
            private_ciphertext=encrypted.ciphertext,
            private_nonce=encrypted.nonce,
            private_key_id=encrypted.key_id,
        ))

    async def list_for_tenant(self, *, action=None, outcome=None, ts_from=None,
                              ts_to=None, before=None, limit=50):
        """RLS already scopes to the tenant. Keyset DESC on (created_at, audit_id);
        `before` = (created_at, audit_id) tuple from the cursor."""
        q = select(AuditLog)
        if action:
            q = q.where(AuditLog.action == action)
        if outcome:
            q = q.where(AuditLog.outcome == outcome)
        if ts_from:
            q = q.where(AuditLog.created_at >= ts_from)
        if ts_to:
            q = q.where(AuditLog.created_at <= ts_to)
        if before:
            cts, cid = before
            q = q.where(
                (AuditLog.created_at < cts)
                | ((AuditLog.created_at == cts) & (AuditLog.audit_id < cid))
            )
        q = q.order_by(AuditLog.created_at.desc(), AuditLog.audit_id.desc()).limit(limit)
        rows = (await self.session.execute(q)).scalars().all()
        result = []
        for row in rows:
            private = await decrypt_audit_payload(
                self.session,
                audit_id=row.audit_id,
                tenant_id=row.tenant_id,
                key_id=row.private_key_id,
                ciphertext=row.private_ciphertext,
                nonce=row.private_nonce,
            )
            result.append(AuditRecord(
                audit_id=row.audit_id,
                tenant_id=row.tenant_id,
                actor_user_id=row.actor_user_id,
                actor_email=private.actor_email,
                action=row.action,
                target_type=row.target_type,
                target_id=row.target_id,
                target_name=private.target_name,
                outcome=row.outcome,
                ip_address=private.ip_address,
                user_agent=private.user_agent,
                request_id=row.request_id,
                meta=private.meta,
                created_at=row.created_at,
            ))
        return result
