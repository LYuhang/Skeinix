"""Identity-side validation helpers for privileged support Sessions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.authorization.types import Action, ResourceType
from vibecanvas_api.config import config
from vibecanvas_api.security.content_encryption import (
    ContentCiphertext,
    content_encryption_service,
)
from vibecanvas_api.storage.models import Session
from vibecanvas_api.storage.models_privileged_access import (
    PlatformAdminEligibility,
    PrivilegedAccessRequest,
)


_FORBIDDEN_ACTIONS = frozenset({
    Action.CREATE,
    Action.TRANSFER,
    Action.MANAGE_ACCESS,
    Action.MANAGE_MEMBERS,
    Action.MANAGE_POLICY,
    Action.PUBLISH,
})
SENSITIVE_ACTIONS = frozenset({
    Action.EXPORT,
    Action.UPDATE,
    Action.DELETE,
    Action.USE,
    Action.EXECUTE,
    Action.CANCEL,
    Action.RESUME,
    Action.DEPLOY,
    Action.MOUNT,
    Action.MANAGE_SECRET,
})


@dataclass(frozen=True, slots=True)
class ActivePrivilegedAccess:
    request_id: str
    organization_id: str
    resource_type: str | None
    resource_id: str | None
    actions: frozenset[Action]
    expires_at: datetime
    approver_user_id: str


async def operator_is_eligible(
    session: AsyncSession,
    user_id: str | uuid.UUID,
) -> bool:
    """Resolve durable reviewed eligibility, expiring it on observation."""
    if not config.privileged_access_enabled:
        return False
    return await platform_role_for_user(session, user_id) is not None


async def platform_role_for_user(
    session: AsyncSession,
    user_id: str | uuid.UUID,
) -> str | None:
    """Return a reviewed platform control-plane role, never tenant access."""
    if not config.privileged_access_enabled:
        return None
    normalized_user_id = str(user_id)
    row = (
        await session.execute(
            select(PlatformAdminEligibility).where(
                PlatformAdminEligibility.platform_user_id
                == uuid.UUID(normalized_user_id),
            )
        )
    ).scalar_one_or_none()
    if row is None or row.status != "active":
        return None
    if row.expires_at <= datetime.now(timezone.utc):
        row.status = "expired"
        row.updated_at = datetime.now(timezone.utc)
        await session.flush()
        return None
    return (
        row.role
        if row.role in {"platform_support", "platform_security_admin"}
        else None
    )


def validate_requested_scope(
    *,
    resource_type: ResourceType | None,
    resource_id: str | None,
    actions: set[Action],
    sensitive_scope_confirmed: bool,
) -> None:
    if not actions or len(actions) > 16:
        raise ValueError("privileged_access_actions_invalid")
    if actions & _FORBIDDEN_ACTIONS:
        raise ValueError("privileged_access_permanent_authority_forbidden")
    if (resource_type is None) != (resource_id is None):
        raise ValueError("privileged_access_resource_scope_invalid")
    if resource_type is None and actions != {Action.VIEW_METADATA}:
        raise ValueError("privileged_access_organization_scope_metadata_only")
    if actions & SENSITIVE_ACTIONS and not sensitive_scope_confirmed:
        raise ValueError("privileged_access_sensitive_scope_confirmation_required")


async def encrypt_request_private_payload(
    session: AsyncSession,
    *,
    request_id: uuid.UUID,
    organization_id: uuid.UUID,
    justification: str,
    ticket_reference: str,
) -> ContentCiphertext:
    return await content_encryption_service().encrypt_json(
        session,
        tenant_id=organization_id,
        resource_type="privileged_access",
        resource_id=str(request_id),
        purpose="request_private",
        record_id=str(request_id),
        value={
            "justification": justification,
            "ticket_reference": ticket_reference,
        },
    )


async def decrypt_request_private_payload(
    session: AsyncSession,
    row: PrivilegedAccessRequest,
) -> dict[str, str]:
    value = await content_encryption_service().decrypt_json(
        session,
        key_id=row.private_key_id,
        tenant_id=row.tenant_id,
        resource_type="privileged_access",
        resource_id=str(row.request_id),
        purpose="request_private",
        record_id=str(row.request_id),
        ciphertext=row.private_ciphertext,
        nonce=row.private_nonce,
    )
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("justification"), str)
        or not isinstance(value.get("ticket_reference"), str)
    ):
        from vibecanvas_api.security.crypto_core import SecretIntegrityError

        raise SecretIntegrityError("privileged access private payload is invalid")
    return {
        "justification": value["justification"],
        "ticket_reference": value["ticket_reference"],
    }


async def resolve_active_privileged_access(
    session: AsyncSession,
    session_row: Session,
) -> ActivePrivilegedAccess | None:
    """Revalidate a support Session against its exact active request."""
    if (
        session_row.audience != "support"
        or session_row.privileged_access_request_id is None
        or session_row.parent_session_id is None
        or session_row.authentication_strength != "webauthn"
    ):
        return None
    now = datetime.now(timezone.utc)
    if not await operator_is_eligible(session, session_row.user_id):
        await session.delete(session_row)
        await session.flush()
        return None
    # This is the only membership-free organization switch. It occurs after
    # validating a support-audience Session row and is still constrained by
    # the request's RLS tenant policy and exact request ID.
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(session_row.active_organization_id)},
    )
    request_row = (
        await session.execute(
            select(PrivilegedAccessRequest).where(
                PrivilegedAccessRequest.request_id
                == session_row.privileged_access_request_id,
                PrivilegedAccessRequest.tenant_id
                == session_row.active_organization_id,
                PrivilegedAccessRequest.operator_user_id == session_row.user_id,
            )
        )
    ).scalar_one_or_none()
    if request_row is None:
        return None
    if (
        request_row.status == "active"
        and request_row.active_expires_at is not None
        and request_row.active_expires_at <= now
    ):
        # Expiry is a durable lifecycle transition, not merely a timestamp
        # checked at the edge. Delete only the derived support Session; its
        # parent Web Session remains available for ordinary operator work.
        request_row.status = "expired"
        request_row.activated_session_id = None
        request_row.updated_at = now
        await session.delete(session_row)
        await session.flush()
        return None
    if (
        request_row.status != "active"
        or request_row.activated_session_id != session_row.session_id
        or request_row.approved_by_user_id is None
        or request_row.approved_by_user_id == request_row.operator_user_id
        or request_row.active_expires_at is None
    ):
        return None
    parent = (
        await session.execute(
            select(Session.session_id).where(
                Session.session_id == session_row.parent_session_id,
                Session.user_id == session_row.user_id,
                Session.audience == "web",
                Session.expires_at > now,
            )
        )
    ).scalar_one_or_none()
    if parent is None or request_row.requested_session_id != parent:
        return None
    try:
        scoped_actions = frozenset(
            Action(value) for value in request_row.allowed_actions
        )
    except ValueError:
        return None
    return ActivePrivilegedAccess(
        request_id=str(request_row.request_id),
        organization_id=str(request_row.tenant_id),
        resource_type=request_row.resource_type,
        resource_id=request_row.resource_id,
        actions=scoped_actions,
        expires_at=request_row.active_expires_at,
        approver_user_id=str(request_row.approved_by_user_id),
    )
