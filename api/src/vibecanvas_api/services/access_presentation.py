"""Human-safe presentation for already-authorized direct access bindings."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from vibecanvas_api.authorization.types import (
    RelationshipBinding,
    RelationshipSubjectType,
)
from vibecanvas_api.schemas.access import DirectBindingOut
from vibecanvas_api.security.identity_protection import decrypt_user_profile
from vibecanvas_api.storage.models import User
from vibecanvas_api.storage.models_org import Group, Organization
from vibecanvas_api.storage.models_service_accounts import ServiceAccount


async def _group_path(session: AsyncSession, group: Group) -> str:
    names = [group.name]
    visited = {group.group_id}
    parent_id = group.parent_group_id
    while parent_id is not None and len(names) < 8:
        if parent_id in visited:
            break
        visited.add(parent_id)
        parent = await session.get(Group, parent_id)
        if parent is None or parent.tenant_id != group.tenant_id:
            break
        names.append(parent.name)
        parent_id = parent.parent_group_id
    return " / ".join(reversed(names))


async def direct_binding_out(
    session: AsyncSession,
    binding: RelationshipBinding,
) -> DirectBindingOut:
    """Resolve display data only for a grant the manager may already list.

    This is not a directory lookup: callers first pass the resource's
    ``manage_access`` authorization and OpenFGA returns only existing direct
    bindings. The helper turns those opaque subjects into revocation-safe,
    human-readable rows.
    """
    display_name = ""
    detail = ""
    try:
        subject_id = uuid.UUID(binding.subject.id)
    except ValueError:
        subject_id = None

    if binding.subject.type is RelationshipSubjectType.USER and subject_id:
        user = await session.get(User, subject_id)
        if user is not None:
            profile = await decrypt_user_profile(session, user)
            display_name = profile.display_name.strip() or profile.email
            detail = profile.email
    elif binding.subject.type is RelationshipSubjectType.GROUP and subject_id:
        group = await session.get(Group, subject_id)
        if group is not None:
            display_name = group.name
            detail = await _group_path(session, group)
    elif (
        binding.subject.type is RelationshipSubjectType.ORGANIZATION
        and subject_id
    ):
        organization = await session.get(Organization, subject_id)
        if organization is not None:
            display_name = organization.name
    elif (
        binding.subject.type is RelationshipSubjectType.SERVICE_ACCOUNT
        and subject_id
    ):
        account = await session.get(ServiceAccount, subject_id)
        if account is not None:
            display_name = account.name
            detail = account.kind

    return DirectBindingOut(
        relation=binding.relation,
        subject_type=binding.subject.type.value,
        subject_id=binding.subject.id,
        subject_relation=binding.subject.relation,
        display_name=display_name,
        detail=detail,
    )
