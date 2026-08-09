"""Explicit-scope authorization adapter for privileged support Sessions."""
from __future__ import annotations

from collections.abc import Sequence

from vibecanvas_api.audit import actions as audit_actions
from vibecanvas_api.audit.context import extract_request_audit_context
from vibecanvas_api.audit.service import record_audit
from vibecanvas_api.auth.deps import AuthContext
from vibecanvas_api.storage.db import session_scope

from .openfga_model import ACTION_RELATIONS, effective_role
from .service import AuthorizationDeniedError, AuthzService
from .types import (
    Action,
    AuthorizationCheck,
    AuthzRequestContext,
    BindingPage,
    Decision,
    PrincipalRef,
    RelationshipBinding,
    ResourceRef,
    ResourceType,
)


class PrivilegedAuthzService:
    """Authorize only one approved temporary scope; never fall back to FGA."""

    def __init__(
        self,
        delegate: AuthzService,
        *,
        session,
        auth: AuthContext,
        request=None,
        audit_uses: bool = True,
    ) -> None:
        if not auth.privileged_access_request_id:
            raise ValueError("privileged authorization requires an active request")
        self._delegate = delegate
        self._session = session
        self._auth = auth
        self._request = request
        self._audit_uses = audit_uses
        self._audited: set[tuple[str, str, str]] = set()

    async def _audit(
        self,
        *,
        resource: ResourceRef,
        action: Action,
        allowed: bool,
    ) -> None:
        if not self._audit_uses:
            return
        key = (resource.type.value, resource.id, action.value)
        if key in self._audited:
            return
        self._audited.add(key)
        # Authorization denial normally aborts the route's business
        # transaction. Keep privileged-use evidence in its own tenant-bound
        # transaction so the denial cannot roll back the very audit record
        # that explains it.
        async with session_scope(
            tenant_id=self._auth.active_organization_id,
        ) as audit_session:
            await record_audit(
                audit_session,
                action=audit_actions.PRIVILEGED_ACCESS_USE,
                actor_user_id=self._auth.user_id,
                actor_email=self._auth.email,
                target_type=resource.type.value,
                target_id=resource.id,
                outcome="success" if allowed else "failure",
                audit_ctx=(
                    extract_request_audit_context(self._request)
                    if self._request is not None
                    else None
                ),
                meta={
                    "privileged_access_request_id": (
                        self._auth.privileged_access_request_id
                    ),
                    "requested_action": action.value,
                    "allowed": allowed,
                },
            )

    async def _decision(
        self,
        *,
        resource: ResourceRef,
        action: Action,
    ) -> Decision:
        if resource.organization_id != self._auth.active_organization_id:
            decision = Decision(False, reason_code="organization_mismatch")
            await self._audit(resource=resource, action=action, allowed=False)
            return decision
        root = await self._delegate.resolve_parent(resource)
        if root is None:
            decision = Decision(False, reason_code="resource_not_found")
            await self._audit(resource=resource, action=action, allowed=False)
            return decision
        allowed_actions = frozenset(
            Action(value) for value in self._auth.privileged_actions
        )
        if self._auth.privileged_resource_type:
            scope_matches = (
                root.type.value == self._auth.privileged_resource_type
                and root.id == self._auth.privileged_resource_id
            )
        else:
            scope_matches = (
                root.type is ResourceType.ORGANIZATION
                and root.id == self._auth.active_organization_id
                and action is Action.VIEW_METADATA
            )
        supported = action in ACTION_RELATIONS.get(root.type, {})
        allowed = scope_matches and supported and action in allowed_actions
        capabilities = frozenset(
            item
            for item in allowed_actions
            if item in ACTION_RELATIONS.get(root.type, {})
        ) if scope_matches else frozenset()
        await self._audit(resource=root, action=action, allowed=allowed)
        return Decision(
            allowed,
            capabilities=capabilities,
            effective_role=(
                effective_role(root.type, capabilities)
                if capabilities
                else None
            ),
            reason_code=(
                "privileged_scope_allow" if allowed
                else "privileged_scope_deny"
            ),
        )

    async def check(
        self,
        principal: PrincipalRef,
        action: Action,
        resource: ResourceRef,
        context: AuthzRequestContext,
    ) -> Decision:
        del principal, context
        return await self._decision(resource=resource, action=action)

    async def require(
        self,
        principal: PrincipalRef,
        action: Action,
        resource: ResourceRef,
        context: AuthzRequestContext,
    ) -> Decision:
        decision = await self.check(principal, action, resource, context)
        if not decision.allowed:
            raise AuthorizationDeniedError(decision)
        return decision

    async def batch_check(
        self,
        checks: Sequence[AuthorizationCheck],
    ) -> tuple[Decision, ...]:
        return tuple([
            await self._decision(resource=item.resource, action=item.action)
            for item in checks
        ])

    async def list_authorized_ids(
        self,
        principal: PrincipalRef,
        action: Action,
        resource_type: ResourceType,
        context: AuthzRequestContext,
    ) -> tuple[str, ...]:
        del principal, context
        if (
            self._auth.privileged_resource_type == resource_type.value
            and action.value in self._auth.privileged_actions
            and self._auth.privileged_resource_id
        ):
            resource = ResourceRef(
                resource_type,
                self._auth.privileged_resource_id,
                self._auth.active_organization_id,
            )
            decision = await self._decision(resource=resource, action=action)
            return (resource.id,) if decision.allowed else ()
        return ()

    async def list_bindings(
        self,
        principal: PrincipalRef,
        resource: ResourceRef,
        context: AuthzRequestContext,
        *,
        continuation_token: str = "",
    ) -> BindingPage:
        del continuation_token
        await self.require(principal, Action.MANAGE_ACCESS, resource, context)
        return BindingPage(())  # pragma: no cover - permanent scope is forbidden

    async def grant(
        self,
        principal: PrincipalRef,
        binding: RelationshipBinding,
        context: AuthzRequestContext,
        *,
        idempotency_key: str,
    ) -> RelationshipBinding:
        del idempotency_key
        await self.require(
            principal, Action.MANAGE_ACCESS, binding.resource, context,
        )
        return binding  # pragma: no cover - permanent scope is forbidden

    async def revoke(
        self,
        principal: PrincipalRef,
        binding: RelationshipBinding,
        context: AuthzRequestContext,
        *,
        idempotency_key: str,
    ) -> RelationshipBinding:
        del idempotency_key
        await self.require(
            principal, Action.MANAGE_ACCESS, binding.resource, context,
        )
        return binding  # pragma: no cover - permanent scope is forbidden

    async def resolve_parent(self, resource: ResourceRef) -> ResourceRef | None:
        return await self._delegate.resolve_parent(resource)


__all__ = ["PrivilegedAuthzService"]
