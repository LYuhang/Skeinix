"""Tenant-scoped audit log route with RLS, filters, and cursor pagination.

RLS already scopes every read to ``app.tenant_id``; this handler only layers
on the optional ``action`` / ``outcome`` / ``from`` / ``to`` filters and the
opaque keyset cursor (DESC on ``(created_at, audit_id)``). Private actor/target,
network context and metadata are decrypted by ``AuditRepo`` only after RLS has
selected rows for the active tenant.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.audit.repo import AuditRepo
from vibecanvas_api.auth.deps import AuthContext, current_user, tenant_db
from vibecanvas_api.authorization.dependencies import (
    authorize_resource,
    get_authz_service,
)
from vibecanvas_api.authorization.service import AuthzService
from vibecanvas_api.authorization.types import Action, ResourceRef, ResourceType
from vibecanvas_api.utils.cursor import decode_cursor, encode_cursor

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("")
async def list_audit(
    request: Request,
    action: str | None = Query(None),
    outcome: str | None = Query(None),
    ts_from: datetime | None = Query(None, alias="from"),
    ts_to: datetime | None = Query(None, alias="to"),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    ctx: AuthContext = Depends(current_user),
    session: AsyncSession = Depends(tenant_db),
    service: AuthzService = Depends(get_authz_service),
):
    await authorize_resource(
        request=request,
        auth=ctx,
        service=service,
        resource=ResourceRef(
            ResourceType.ORGANIZATION,
            ctx.active_organization_id,
            ctx.active_organization_id,
        ),
        action=Action.VIEW_AUDIT,
    )
    # ``before`` is the (created_at, audit_id) pair the cursor encodes; a bad
    # cursor → 400 (decode_cursor raises HTTPException).
    before = decode_cursor(cursor) if cursor else None
    # Fetch one extra to learn whether there is a next page.
    rows = await AuditRepo(session).list_for_tenant(
        action=action,
        outcome=outcome,
        ts_from=ts_from,
        ts_to=ts_to,
        before=before,
        limit=limit + 1,
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (
        encode_cursor(rows[-1].created_at, rows[-1].audit_id)
        if has_more and rows
        else None
    )
    return {
        "items": [
            {
                "audit_id": str(r.audit_id),
                "action": r.action,
                "actor_email": r.actor_email,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "target_name": r.target_name,
                "outcome": r.outcome,
                "ip_address": r.ip_address,
                "request_id": r.request_id,
                "meta": r.meta,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }
