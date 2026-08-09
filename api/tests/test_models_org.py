"""Organization identity ORM registration tests.

Pure metadata assertions (no DB) — confirm the new tables register on the
shared ``Base.metadata`` via the tail-imports.
"""
from vibecanvas_api.storage.models import Base
from vibecanvas_api.storage import models_org  # noqa: F401  (registers tables)


def test_organization_and_group_tables_registered():
    tables = Base.metadata.tables
    assert "organizations" in tables
    assert "groups" in tables
    org = tables["organizations"]
    assert {
        "tenant_id",
        "kind",
        "slug",
        "name",
        "created_by",
        "created_at",
        "updated_at",
    } <= set(org.columns.keys())
    group = tables["groups"]
    assert {
        "group_id",
        "tenant_id",
        "parent_group_id",
        "kind",
        "name",
        "source",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    } <= set(group.columns.keys())


def test_membership_tables_registered():
    tables = Base.metadata.tables
    assert "org_memberships" in tables
    assert "group_memberships" in tables
    om = tables["org_memberships"]
    assert {
        "membership_id",
        "user_id",
        "tenant_id",
        "org_role",
        "status",
        "invited_by",
        "created_at",
        "updated_at",
    } <= set(om.columns.keys())
    gm = tables["group_memberships"]
    assert {
        "membership_id",
        "user_id",
        "group_id",
        "tenant_id",
        "group_role",
        "status",
        "created_at",
        "updated_at",
    } <= set(gm.columns.keys())
    # uniqueness on (user_id, tenant_id)
    uniq = {tuple(sorted(c.name for c in con.columns)) for con in om.constraints
            if con.__class__.__name__ == "UniqueConstraint"}
    assert ("tenant_id", "user_id") in uniq


def test_owner_id_on_shareable_resources():
    tables = Base.metadata.tables
    for t in ("templates", "workflows", "tasks", "deployments"):
        assert "owner_id" in tables[t].columns.keys(), f"{t} missing owner_id"
