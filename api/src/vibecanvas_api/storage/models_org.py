"""Organization, group, and membership identity models.

`tenant_id` is the hard isolation key, reinterpreted as the ORGANIZATION.
RLS policies and graph integrity constraints live in migrations 022 and 055.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from vibecanvas_api.storage.models import Base


class Organization(Base):
    __tablename__ = "organizations"
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False, server_default="personal")
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now())


class Group(Base):
    __tablename__ = "groups"
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False)
    parent_group_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="team")
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="native")
    directory_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "enterprise_identity_providers.provider_id",
            ondelete="CASCADE",
            name="fk_groups_directory_provider",
        ),
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    external_id_lookup_hash: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active")
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("group_id", "tenant_id", name="uq_groups_id_tenant"),
        UniqueConstraint(
            "tenant_id", "parent_group_id", "name",
            name="uq_groups_parent_name",
        ),
        ForeignKeyConstraint(
            ["parent_group_id", "tenant_id"],
            ["groups.group_id", "groups.tenant_id"],
            name="fk_groups_parent_same_organization",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "kind IN ('department','team')",
            name="ck_groups_kind",
        ),
        CheckConstraint(
            "source IN ('native','idp')",
            name="ck_groups_source",
        ),
        CheckConstraint(
            "status IN ('active','archived')",
            name="ck_groups_status",
        ),
        CheckConstraint(
            "(source = 'native' AND directory_provider_id IS NULL "
            "AND external_id IS NULL AND external_id_lookup_hash IS NULL) OR "
            "(source = 'idp' AND directory_provider_id IS NOT NULL "
            "AND external_id_lookup_hash IS NOT NULL)",
            name="ck_groups_directory_source",
        ),
        UniqueConstraint(
            "directory_provider_id",
            "external_id_lookup_hash",
            name="uq_groups_directory_external_id",
        ),
    )


class OrgMembership(Base):
    __tablename__ = "org_memberships"
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False)
    org_role: Mapped[str] = mapped_column(Text, nullable=False, server_default="member")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active")
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="native",
    )
    directory_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "enterprise_identity_providers.provider_id",
            ondelete="CASCADE",
            name="fk_org_memberships_directory_provider",
        ),
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("user_id", "tenant_id", name="uq_org_membership"),
        CheckConstraint(
            "source IN ('native','scim')",
            name="ck_org_memberships_source",
        ),
        CheckConstraint(
            "(source = 'native' AND directory_provider_id IS NULL) OR "
            "(source = 'scim' AND directory_provider_id IS NOT NULL)",
            name="ck_org_memberships_directory_source",
        ),
    )


class GroupMembership(Base):
    __tablename__ = "group_memberships"
    membership_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False)
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False)
    group_role: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="member")
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="active")
    source: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="native",
    )
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_group_membership"),
        ForeignKeyConstraint(
            ["group_id", "tenant_id"],
            ["groups.group_id", "groups.tenant_id"],
            name="fk_group_membership_same_organization",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "group_role IN ('lead','member')",
            name="ck_group_membership_role",
        ),
        CheckConstraint(
            "status IN ('active','suspended','revoked')",
            name="ck_group_membership_status",
        ),
        CheckConstraint(
            "source IN ('native','idp')",
            name="ck_group_membership_source",
        ),
    )
