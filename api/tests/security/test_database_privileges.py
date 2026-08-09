from __future__ import annotations

import pytest

from vibecanvas_api.security import database_privileges as subject


def _facts(**overrides) -> subject.DatabaseRoleFacts:
    values = {
        "role_name": "vibecanvas_app",
        "is_superuser": False,
        "bypasses_rls": False,
        "can_create_database": False,
        "can_create_role": False,
        "can_replicate": False,
        "can_create_in_database": False,
        "can_create_in_schema": False,
        "owns_schema_objects": False,
        "can_assume_dangerous_role": False,
    }
    values.update(overrides)
    return subject.DatabaseRoleFacts(**values)


@pytest.mark.asyncio
async def test_runtime_role_accepts_only_rls_bound_dml_identity(monkeypatch):
    async def inspect(_engine):
        return _facts()

    monkeypatch.setattr(subject, "inspect_database_role", inspect)
    await subject.verify_database_role(object(), mode="runtime")


@pytest.mark.asyncio
async def test_runtime_role_rejects_owner_ddl_and_rls_bypass(monkeypatch):
    async def inspect(_engine):
        return _facts(
            bypasses_rls=True,
            can_create_in_schema=True,
            owns_schema_objects=True,
        )

    monkeypatch.setattr(subject, "inspect_database_role", inspect)
    with pytest.raises(subject.DatabasePrivilegeError) as exc_info:
        await subject.verify_database_role(object(), mode="runtime")
    assert exc_info.value.codes == (
        "schema_create",
        "object_owner",
        "bypassrls",
    )


@pytest.mark.asyncio
async def test_maintenance_role_is_bypass_only_without_ddl(monkeypatch):
    async def inspect(_engine):
        return _facts(role_name="vibecanvas_maintenance", bypasses_rls=True)

    monkeypatch.setattr(subject, "inspect_database_role", inspect)
    await subject.verify_database_role(object(), mode="maintenance")


@pytest.mark.asyncio
async def test_maintenance_role_rejects_superuser(monkeypatch):
    async def inspect(_engine):
        return _facts(is_superuser=True, bypasses_rls=True)

    monkeypatch.setattr(subject, "inspect_database_role", inspect)
    with pytest.raises(subject.DatabasePrivilegeError) as exc_info:
        await subject.verify_database_role(object(), mode="maintenance")
    assert exc_info.value.codes == ("superuser",)


@pytest.mark.asyncio
async def test_runtime_rejects_indirect_set_role_escalation(monkeypatch):
    async def inspect(_engine):
        return _facts(can_assume_dangerous_role=True)

    monkeypatch.setattr(subject, "inspect_database_role", inspect)
    with pytest.raises(subject.DatabasePrivilegeError) as exc_info:
        await subject.verify_database_role(object(), mode="runtime")
    assert exc_info.value.codes == ("dangerous_role_membership",)
