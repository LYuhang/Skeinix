"""Organization, membership, and generic Group API projections."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .access import ResourceAccessOut


OrganizationRole = Literal["owner", "admin", "member", "guest", "auditor"]
OrganizationMembershipStatus = Literal[
    "invited",
    "active",
    "suspended",
    "revoking",
    "revoked",
]


class OrganizationOut(BaseModel):
    organization_id: str
    kind: Literal["personal", "business"]
    slug: str
    name: str
    membership_id: str
    role: OrganizationRole
    status: OrganizationMembershipStatus
    active: bool
    access: ResourceAccessOut


class OrganizationListOut(BaseModel):
    items: list[OrganizationOut] = Field(default_factory=list)
    active_organization_id: str
    session_generation: int = Field(ge=1)


class OrganizationSwitchOut(OrganizationOut):
    session_generation: int = Field(ge=1)


class OrganizationMemberOut(BaseModel):
    membership_id: str
    user_id: str
    email: str
    display_name: str
    role: OrganizationRole
    status: OrganizationMembershipStatus
    source: Literal["native", "scim"]
    directory_provider_id: str | None = None
    created_at: datetime
    updated_at: datetime


class OrganizationMemberListOut(BaseModel):
    items: list[OrganizationMemberOut] = Field(default_factory=list)


class OrganizationSelfGroupOut(BaseModel):
    group_id: str
    kind: Literal["department", "team"]
    name: str
    source: Literal["native", "idp"]
    role: Literal["lead", "member"]
    status: Literal["active", "suspended", "revoked"]


class OrganizationSelfOut(BaseModel):
    """The current user's own membership projection.

    This deliberately excludes the organization member directory, audit data,
    service accounts, and every other user's profile.
    """

    membership: OrganizationMemberOut
    groups: list[OrganizationSelfGroupOut] = Field(default_factory=list)


class GroupOut(BaseModel):
    group_id: str
    organization_id: str
    parent_group_id: str | None = None
    kind: Literal["department", "team"]
    name: str
    source: Literal["native", "idp"]
    directory_provider_id: str | None = None
    external_id: str | None = None
    status: Literal["active", "archived"]
    created_by: str
    created_at: datetime
    updated_at: datetime
    access: ResourceAccessOut


class GroupListOut(BaseModel):
    items: list[GroupOut] = Field(default_factory=list)


class GroupMemberOut(BaseModel):
    membership_id: str
    user_id: str
    email: str
    display_name: str
    role: Literal["lead", "member"]
    status: Literal["active", "suspended", "revoked"]
    created_at: datetime
    updated_at: datetime


class GroupMemberListOut(BaseModel):
    items: list[GroupMemberOut] = Field(default_factory=list)


class GroupMembershipMutationOut(BaseModel):
    membership_id: str
    group_id: str
    user_id: str
    role: Literal["lead", "member"]
    status: Literal["active", "suspended", "revoked"]


class ServiceAccountOut(BaseModel):
    service_account_id: str
    name: str
    kind: Literal["deployment", "schedule", "task", "integration"]
    owner_resource_type: Literal["deployment", "task", "integration"]
    owner_resource_id: str
    status: Literal["active", "disabled", "deleted"]
    generation: int = Field(ge=1)
    created_by: str
    credential_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    disabled_at: datetime | None = None


class ServiceAccountListOut(BaseModel):
    items: list[ServiceAccountOut] = Field(default_factory=list)


class ServiceAccountStatusBody(BaseModel):
    status: Literal["active", "disabled"]
