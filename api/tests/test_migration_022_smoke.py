"""Current-head authorization migration contract.

Revision 022 is historical input.  The supported runtime schema is the head
schema after revisions 055/057 retired departments and ``resource_grants``.
These tests intentionally do not downgrade or replay retired SQL against the
current schema: strict/irreversible cutovers must remain irreversible.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _migration_module(revision: str, filename: str):
    path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / filename
    )
    spec = importlib.util.spec_from_file_location(f"_migration_{revision}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


async def test_022_foundation_migrated_to_generic_group_tables(pg_engine):
    async with pg_engine.connect() as connection:
        present = {
            row[0]
            for row in (
                await connection.execute(
                    text(
                        "SELECT tablename FROM pg_tables "
                        "WHERE schemaname='public' AND tablename = ANY(:names)"
                    ),
                    {
                        "names": [
                            "organizations",
                            "groups",
                            "org_memberships",
                            "group_memberships",
                            "authz_mutations",
                            "authz_edge_revisions",
                        ]
                    },
                )
            ).all()
        }
        retired = (
            await connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname='public' AND tablename = ANY(:names)"
                ),
                {"names": ["departments", "dept_memberships", "resource_grants"]},
            )
        ).all()
    assert present == {
        "organizations",
        "groups",
        "org_memberships",
        "group_memberships",
        "authz_mutations",
        "authz_edge_revisions",
    }
    assert retired == []


async def test_authorization_ledger_force_rls(pg_engine):
    async with pg_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT relname, relforcerowsecurity, relrowsecurity "
                    "FROM pg_class WHERE relname = ANY(:names)"
                ),
                {"names": ["authz_mutations", "authz_edge_revisions"]},
            )
        ).all()
    assert {name: (forced, enabled) for name, forced, enabled in rows} == {
        "authz_mutations": (True, True),
        "authz_edge_revisions": (True, True),
    }


async def test_022_owner_id_columns_added(pg_engine):
    async with pg_engine.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT table_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND column_name='owner_id' "
                    "AND table_name = ANY(:names)"
                ),
                {"names": ["templates", "workflows", "tasks", "deployments"]},
            )
        ).all()
    assert {row[0] for row in rows} == {
        "templates",
        "workflows",
        "tasks",
        "deployments",
    }


async def test_022_backfill_personal_org_and_owner(pg_engine):
    """The historical upgrade still contains the one-time org backfill."""
    module = _migration_module("022", "022_org_permissions_foundation.py")
    sql = "\n".join(module.BACKFILL_SQL).lower()
    assert "insert into organizations" in sql
    assert "insert into org_memberships" in sql
    assert "'owner'" in sql


async def test_022_backfill_owner_id_survives_grant_retirement(pg_engine):
    module = _migration_module("022", "022_org_permissions_foundation.py")
    sql = "\n".join(module.BACKFILL_SQL).lower()
    for table in ("templates", "workflows", "tasks", "deployments"):
        assert f"update {table}" in sql
        assert "owner_id" in sql
    async with pg_engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT to_regclass('public.resource_grants')"))
        ).scalar_one() is None


async def test_057_converts_non_public_grants_before_dropping_legacy_table(
    pg_engine,
):
    """057 remains a one-way migration; runtime never recreates the old ACL."""
    source = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "057_retire_legacy_resource_grants.py"
    ).read_text(encoding="utf-8").lower()
    assert "principal_type <> 'public'" in source
    assert "insert into authz_mutations" in source
    assert "'direct_binding'" in source
    assert "drop table resource_grants" in source
    assert "intentionally irreversible" in source
    async with pg_engine.connect() as connection:
        assert (
            await connection.execute(text("SELECT to_regclass('public.resource_grants')"))
        ).scalar_one() is None


async def test_055_reconciles_post_022_accounts_before_session_fk(pg_engine):
    """Valid tenants/users created on revisions 022-054 survive the cutover."""
    source = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "055_organization_sessions_and_groups.py"
    ).read_text(encoding="utf-8").lower()
    organizations_backfill = source.index("insert into organizations")
    memberships_backfill = source.index("insert into org_memberships")
    active_org_fk = source.index("add constraint fk_sessions_active_organization")
    assert organizations_backfill < active_org_fk
    assert memberships_backfill < active_org_fk
    assert "on conflict (tenant_id) do nothing" in source
    assert "on conflict (user_id, tenant_id) do nothing" in source


async def test_audit_cutover_temporarily_scopes_migrator_write(pg_engine):
    versions = Path(__file__).resolve().parent.parent / "alembic" / "versions"
    staging = (versions / "097_audit_private_payload_encryption.py").read_text(
        encoding="utf-8"
    ).lower()
    strict = (versions / "098_strict_audit_private_payload_encryption.py").read_text(
        encoding="utf-8"
    ).lower()
    assert "grant select, update" in staging
    assert "to vibecanvas_migrator" in staging
    assert "revoke update, delete" in strict
    assert "from vibecanvas_migrator" in strict
