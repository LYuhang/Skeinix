"""Central, OpenFGA-backed resource-authorization seam."""

from .service import AuthzService
from .types import (
    Action,
    AuthzRequestContext,
    Decision,
    PrincipalRef,
    PrincipalType,
    ResourceRef,
    ResourceType,
)

__all__ = [
    "Action",
    "AuthzRequestContext",
    "AuthzService",
    "Decision",
    "PrincipalRef",
    "PrincipalType",
    "ResourceRef",
    "ResourceType",
]
