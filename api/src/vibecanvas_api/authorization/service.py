"""Application-owned authorization protocol backed exclusively by OpenFGA."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

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


class AuthzService(Protocol):
    async def check(
        self,
        principal: PrincipalRef,
        action: Action,
        resource: ResourceRef,
        context: AuthzRequestContext,
    ) -> Decision: ...

    async def require(
        self,
        principal: PrincipalRef,
        action: Action,
        resource: ResourceRef,
        context: AuthzRequestContext,
    ) -> Decision: ...

    async def batch_check(
        self,
        checks: Sequence[AuthorizationCheck],
    ) -> tuple[Decision, ...]: ...

    async def list_authorized_ids(
        self,
        principal: PrincipalRef,
        action: Action,
        resource_type: ResourceType,
        context: AuthzRequestContext,
    ) -> tuple[str, ...]: ...

    async def list_bindings(
        self,
        principal: PrincipalRef,
        resource: ResourceRef,
        context: AuthzRequestContext,
        *,
        continuation_token: str = "",
    ) -> BindingPage: ...

    async def grant(
        self,
        principal: PrincipalRef,
        binding: RelationshipBinding,
        context: AuthzRequestContext,
        *,
        idempotency_key: str,
    ) -> RelationshipBinding: ...

    async def revoke(
        self,
        principal: PrincipalRef,
        binding: RelationshipBinding,
        context: AuthzRequestContext,
        *,
        idempotency_key: str,
    ) -> RelationshipBinding: ...

    async def resolve_parent(
        self,
        resource: ResourceRef,
    ) -> ResourceRef | None: ...


async def batch_resource_decisions(
    service: AuthzService,
    *,
    principal: PrincipalRef,
    resources: Sequence[ResourceRef],
    context: AuthzRequestContext,
) -> dict[ResourceRef, Decision]:
    """Resolve complete capabilities for a page without remote N+1 checks."""
    if not resources:
        return {}
    from .openfga_model import ACTION_RELATIONS, effective_role

    checks: list[AuthorizationCheck] = []
    keys: list[tuple[ResourceRef, Action]] = []
    for resource in resources:
        for action in ACTION_RELATIONS.get(resource.type, {}):
            checks.append(AuthorizationCheck(
                principal=principal,
                action=action,
                resource=resource,
                context=context,
                consistency=context.consistency,
            ))
            keys.append((resource, action))
    checked = await service.batch_check(checks)
    allowed: dict[ResourceRef, set[Action]] = {
        resource: set() for resource in resources
    }
    for (resource, action), decision in zip(keys, checked, strict=True):
        if decision.allowed:
            allowed[resource].add(action)
    return {
        resource: Decision(
            allowed=bool(actions),
            capabilities=frozenset(actions),
            effective_role=effective_role(resource.type, frozenset(actions)),
            reason_code=(
                "batch_capabilities_allow" if actions else "batch_capabilities_deny"
            ),
        )
        for resource, actions in allowed.items()
    }


class AuthorizationDeniedError(PermissionError):
    def __init__(self, decision: Decision) -> None:
        super().__init__(decision.reason_code or "permission_denied")
        self.decision = decision
