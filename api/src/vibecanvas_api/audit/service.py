"""Audit-log write helpers for request and resource operations.

* record_audit         — resource events; encrypted ORM add into the caller's
                          tenant session (atomic with the action). Async.
* record_auth_audit    — auth events; standalone admin-engine raw text() INSERT
                          (pre-tenant-context; failures; nullable tenant_id).
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from vibecanvas_api.audit.repo import AuditRepo
from vibecanvas_api.security.audit_protection import audit_lookup_digest
from vibecanvas_api.security.redaction import redact_text, redact_value
from vibecanvas_api.services.tenant_db import session_scope_admin
from vibecanvas_api.storage.db import session_scope

_log = logging.getLogger(__name__)


async def record_audit(session, *, action, actor_user_id, actor_email,
                       target_type=None, target_id=None, target_name=None,
                       outcome="success", audit_ctx=None, meta=None) -> None:
    """Resource-path audit: encrypted ORM add into ``session`` (tenant_db
    session) — commits atomically with the action via the tenant_db teardown.
    tenant_id is OMITTED so the RLS server-default fills it from app.tenant_id.
    MUST be exception-tight (a raise rolls back the action too): only
    fixed-shape, validated data — never the request body / secrets. Private
    display fields, network context and metadata share one tenant envelope."""
    await AuditRepo(session).add_row(
        action=action, actor_user_id=actor_user_id, actor_email=actor_email,
        target_type=target_type, target_id=target_id, target_name=target_name,
        outcome=outcome,
        ip_address=getattr(audit_ctx, "ip_address", None),
        user_agent=getattr(audit_ctx, "user_agent", None),
        request_id=getattr(audit_ctx, "request_id", None),
        meta=redact_value(meta or {}, redact_content=True),
    )


async def record_auth_audit(*, action, actor_user_id, actor_email, tenant_id,
                            outcome, audit_ctx=None, meta=None) -> None:
    """Auth-path audit: standalone admin-engine transaction, RAW text() INSERT
    listing tenant_id explicitly so an explicit NULL sticks (the ORM would omit
    the column → server-default fires → no NULL / 22P02). meta is json.dumps'd
    (asyncpg text-protocol won't auto-encode a dict). No CAST needed — direct
    column insert infers types. Fail-soft: never block the auth response, but
    log on failure so the gap is visible."""
    try:
        # Known-tenant auth events do not need an RLS-bypassing connection.
        # Binding the ordinary app session is both least-privilege and keeps
        # registration/login audit reliable when ADMIN_DATABASE_URL is absent.
        # Only truly pre-tenant events (for example an unknown login email)
        # require the dedicated audit/admin path so NULL remains representable.
        if tenant_id is not None:
            async with session_scope(tenant_id=str(tenant_id)) as s:
                await AuditRepo(s).add_row(
                    action=action,
                    actor_user_id=actor_user_id,
                    actor_email=actor_email,
                    target_type=None,
                    target_id=None,
                    target_name=None,
                    outcome=outcome,
                    ip_address=getattr(audit_ctx, "ip_address", None),
                    user_agent=getattr(audit_ctx, "user_agent", None),
                    request_id=getattr(audit_ctx, "request_id", None),
                    meta=redact_value(meta or {}, redact_content=True),
                )
        else:
            # Unknown identities have no tenant key. Preserve only structural
            # audit data and irreversible correlation digests.
            async with session_scope_admin() as s:
                await s.execute(text(
                    "INSERT INTO audit_log "
                    "(tenant_id, actor_user_id, actor_email, "
                    "actor_lookup_hash, action, outcome, ip_address, "
                    "ip_lookup_hash, user_agent, request_id, meta) "
                    "VALUES (NULL, :uid, NULL, :email_hash, :action, :outcome, "
                    "NULL, :ip_hash, NULL, :rid, '{}'::jsonb)"
                ), {
                    "uid": actor_user_id,
                    "email_hash": audit_lookup_digest(
                        "actor_email", actor_email
                    ),
                    "action": action,
                    "outcome": outcome,
                    "ip_hash": audit_lookup_digest(
                        "ip_address", getattr(audit_ctx, "ip_address", None)
                    ),
                    "rid": getattr(audit_ctx, "request_id", None),
                })
    except Exception:
        _log.error("auth audit write failed (event still occurred): action=%s "
                   "outcome=%s", redact_text(str(action)),
                   redact_text(str(outcome)), exc_info=True)
