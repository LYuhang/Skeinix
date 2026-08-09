"""One fail-closed identity revalidation seam for derived capabilities."""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.auth.deps import AuthContext
from vibecanvas_api.auth.repo import AuthRepo
from vibecanvas_api.storage.models import Session
from vibecanvas_api.storage.models_org import OrgMembership


class LiveIdentityError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def resolve_live_authorization_identity(
    session: AsyncSession,
    *,
    session_id: str,
    user_id: str,
    organization_id: str,
    session_generation: int,
    membership_id: str,
) -> AuthContext:
    """Resolve ordinary membership or an active privileged-support scope.

    Signed capability claims are only lookup keys. Durable Session,
    membership/request status, generation, expiry, and user lifecycle are
    re-read for every broker call.
    """
    try:
        session_uuid = uuid.UUID(session_id)
        user_uuid = uuid.UUID(user_id)
        organization_uuid = uuid.UUID(organization_id)
    except (TypeError, ValueError) as exc:
        raise LiveIdentityError("identity_invalid") from exc
    session_row = (
        await session.execute(
            select(Session).where(
                Session.session_id == session_uuid,
                Session.user_id == user_uuid,
                Session.active_organization_id == organization_uuid,
                Session.generation == session_generation,
                Session.expires_at > datetime.now(timezone.utc),
            )
        )
    ).scalar_one_or_none()
    if session_row is None:
        raise LiveIdentityError("session_revoked")
    user = await AuthRepo(session).get_user(user_uuid)
    if user is None or user.status != "active":
        raise LiveIdentityError("user_revoked")
    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )
    if session_row.audience == "support":
        from vibecanvas_api.auth.privileged_access import (
            resolve_active_privileged_access,
        )

        privileged = await resolve_active_privileged_access(session, session_row)
        if (
            privileged is None
            or membership_id != f"privileged:{privileged.request_id}"
        ):
            raise LiveIdentityError("privileged_access_revoked")
        return AuthContext(
            user_id=user_id,
            tenant_id=organization_id,
            email=user.email,
            display_name=user.display_name,
            active_organization_id=organization_id,
            membership_id=membership_id,
            membership_role="privileged_support",
            membership_status="active",
            session_generation=int(session_row.generation),
            authentication_strength=session_row.authentication_strength,
            step_up_expires_at=session_row.step_up_expires_at,
            session_id=session_id,
            session_audience="support",
            privileged_access_request_id=privileged.request_id,
            privileged_resource_type=privileged.resource_type or "",
            privileged_resource_id=privileged.resource_id or "",
            privileged_actions=frozenset(
                action.value for action in privileged.actions
            ),
            privileged_expires_at=privileged.expires_at,
        )
    try:
        membership_uuid = uuid.UUID(membership_id)
    except (TypeError, ValueError) as exc:
        raise LiveIdentityError("membership_invalid") from exc
    membership = (
        await session.execute(
            select(OrgMembership).where(
                OrgMembership.membership_id == membership_uuid,
                OrgMembership.user_id == user_uuid,
                OrgMembership.tenant_id == organization_uuid,
                OrgMembership.status == "active",
            )
        )
    ).scalar_one_or_none()
    if membership is None:
        raise LiveIdentityError("membership_revoked")
    return AuthContext(
        user_id=user_id,
        tenant_id=organization_id,
        email=user.email,
        display_name=user.display_name,
        active_organization_id=organization_id,
        membership_id=str(membership.membership_id),
        membership_role=membership.org_role,
        membership_status=membership.status,
        session_generation=int(session_row.generation),
        authentication_strength=session_row.authentication_strength,
        step_up_expires_at=session_row.step_up_expires_at,
        session_id=session_id,
        session_audience=session_row.audience,
    )


__all__ = [
    "LiveIdentityError",
    "resolve_live_authorization_identity",
]
