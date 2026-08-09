"""Deployments T1 — FORCE RLS isolation + soft-delete filter + api_key_hash unique.

Mirrors ``test_tasks_table_rls.py``. The ``app_engine``
fixture connects as the non-superuser ``vibecanvas_app`` role (the
table owner); ``FORCE ROW LEVEL SECURITY`` binds even the owner, so
cross-tenant rows are invisible.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


async def _seed_tenant_user(app_engine, tenant_id: uuid.UUID,
                            user_id: uuid.UUID, email_prefix: str) -> None:
    """Create one tenant + one user under it. Auth tables are RLS-free,
    so a plain begin() block (no app.tenant_id) is fine."""
    async with app_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tenant_id},
        )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": user_id, "t": tenant_id,
             "e": f"{email_prefix}-{uuid.uuid4().hex[:8]}@example.com"},
        )


async def _seed_workflow(app_engine, wf_id: str, tenant_id: uuid.UUID,
                         user_id: uuid.UUID) -> None:
    """Insert a workflows row under the given tenant (RLS-bound: must set
    app.tenant_id first)."""
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.workflow_repo import WorkflowRepo

    async with session_scope(tenant_id=str(tenant_id)) as session:
        await WorkflowRepo(session, str(user_id)).create_workflow(
            wf_id=wf_id,
            name="Deployment Workflow",
        )


@pytest.mark.asyncio
async def test_deployments_isolated_across_tenants(app_engine):
    """A deployment written under tenant A is invisible to tenant B."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    u_a = uuid.uuid4()
    wf_a = f"wf_a_{uuid.uuid4().hex[:8]}"

    # Seed: 2 tenants, 1 user in tenant A.
    async with app_engine.begin() as c:
        for t in (t_a, t_b):
            await c.execute(
                text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
                {"t": t},
            )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": u_a, "t": t_a,
             "e": f"dep-{uuid.uuid4().hex[:8]}@example.com"},
        )
    await _seed_workflow(app_engine, wf_a, t_a, u_a)

    # Tenant A writes a deployment.
    dep_id = uuid.uuid4()
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                "name, slug, trigger_type, version_pin, pinned_major, "
                "pinned_sub, api_key_hash) "
                "VALUES (:id, :t, :u, :u, :w, 'A bot', 'a-bot', 'api', "
                "'specific', 1, 0, 'hash-a')"
            ),
            {"id": dep_id, "t": t_a, "u": u_a, "w": wf_a},
        )
        await c.commit()

    # Tenant B → must see zero.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_b)},
        )
        rows = (await c.execute(text("SELECT id FROM deployments"))).all()
    assert rows == [], f"RLS leak — tenant B saw {rows}"

    # Tenant A → sees its own row.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        rows = (await c.execute(text("SELECT id FROM deployments"))).all()
    assert [r[0] for r in rows] == [dep_id]


@pytest.mark.asyncio
async def test_soft_delete_filter(app_engine):
    """Caller-side ``deleted_at IS NULL`` filter excludes soft-deleted rows."""
    t_a = uuid.uuid4()
    u_a = uuid.uuid4()
    wf_a = f"wf_sd_{uuid.uuid4().hex[:8]}"
    await _seed_tenant_user(app_engine, t_a, u_a, "sd")
    await _seed_workflow(app_engine, wf_a, t_a, u_a)

    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                "name, slug, trigger_type, version_pin, pinned_major, "
                "pinned_sub, api_key_hash, deleted_at) "
                "VALUES (:id, :t, :u, :u, :w, 'Gone', 'gone', 'api', "
                "'specific', 1, 0, 'hash-g', now())"
            ),
            {"id": uuid.uuid4(), "t": t_a, "u": u_a, "w": wf_a},
        )
        await c.commit()

        live = (await c.execute(text(
            "SELECT id FROM deployments WHERE deleted_at IS NULL"
        ))).all()
        all_rows = (await c.execute(text(
            "SELECT id FROM deployments"
        ))).all()
    assert live == [], "Soft-deleted rows must be filtered out by callers"
    assert len(all_rows) == 1, "But the row physically still exists"


@pytest.mark.asyncio
async def test_api_key_hash_unique_among_live_rows(app_engine):
    """Partial UNIQUE on api_key_hash WHERE deleted_at IS NULL prevents dupes
    among live rows (but allows reuse after soft-delete)."""
    t_a = uuid.uuid4()
    u_a = uuid.uuid4()
    wf_a = f"wf_u_{uuid.uuid4().hex[:8]}"
    await _seed_tenant_user(app_engine, t_a, u_a, "unq")
    await _seed_workflow(app_engine, wf_a, t_a, u_a)

    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                "name, slug, trigger_type, version_pin, pinned_major, "
                "pinned_sub, api_key_hash) "
                "VALUES (:id, :t, :u, :u, :w, 'K1', 'k1', 'api', 'specific', "
                "1, 0, 'samehash')"
            ),
            {"id": uuid.uuid4(), "t": t_a, "u": u_a, "w": wf_a},
        )
        await c.commit()

    # A second live row with the same api_key_hash must violate the partial
    # UNIQUE — verified inside its own transaction so the IntegrityError
    # rollback doesn't poison earlier state.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        with pytest.raises(IntegrityError):
            await c.execute(
                text(
                    "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                    "name, slug, trigger_type, version_pin, pinned_major, "
                    "pinned_sub, api_key_hash) "
                    "VALUES (:id, :t, :u, :u, :w, 'K2', 'k2', 'api', "
                    "'specific', 1, 0, 'samehash')"
                ),
                {"id": uuid.uuid4(), "t": t_a, "u": u_a, "w": wf_a},
            )
            await c.commit()


@pytest.mark.asyncio
async def test_slug_is_globally_unique_only_among_live(app_engine):
    """Public deployment slugs are globally unique and reusable after delete."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    u_a, u_b = uuid.uuid4(), uuid.uuid4()
    wf_a = f"wf_slug_a_{uuid.uuid4().hex[:8]}"
    wf_b = f"wf_slug_b_{uuid.uuid4().hex[:8]}"

    async with app_engine.begin() as c:
        for t in (t_a, t_b):
            await c.execute(
                text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
                {"t": t},
            )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": u_a, "t": t_a, "e": f"sa-{uuid.uuid4().hex[:8]}@example.com"},
        )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {"u": u_b, "t": t_b, "e": f"sb-{uuid.uuid4().hex[:8]}@example.com"},
        )
    await _seed_workflow(app_engine, wf_a, t_a, u_a)
    await _seed_workflow(app_engine, wf_b, t_b, u_b)

    dep_a = uuid.uuid4()

    # Tenant A: deploy with slug "shared".
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                "name, slug, trigger_type, version_pin, pinned_major, "
                "pinned_sub, api_key_hash) "
                "VALUES (:id, :t, :u, :u, :w, 'A', 'shared', 'api', "
                "'specific', 1, 0, 'hash-shared-a')"
            ),
            {"id": dep_a, "t": t_a, "u": u_a, "w": wf_a},
        )
        await c.commit()

    # Tenant B: the same public slug conflicts even though RLS hides tenant A.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_b)},
        )
        with pytest.raises(IntegrityError):
            await c.execute(
                text(
                    "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                    "name, slug, trigger_type, version_pin, pinned_major, "
                    "pinned_sub, api_key_hash) "
                    "VALUES (:id, :t, :u, :u, :w, 'B', 'shared', 'api', "
                    "'specific', 1, 0, 'hash-shared-b')"
                ),
                {"id": uuid.uuid4(), "t": t_b, "u": u_b, "w": wf_b},
            )
            await c.commit()
        await c.rollback()

    # Soft-delete the original row so the public slug can be reused.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text("UPDATE deployments SET deleted_at = now() WHERE id = :id"),
            {"id": dep_a},
        )
        await c.commit()

    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_b)},
        )
        await c.execute(
            text(
                "INSERT INTO deployments(id, tenant_id, user_id, owner_id, wf_id, "
                "name, slug, trigger_type, version_pin, pinned_major, "
                "pinned_sub, api_key_hash) "
                "VALUES (:id, :t, :u, :u, :w, 'B', 'shared', 'api', "
                "'specific', 1, 0, 'hash-shared-b')"
            ),
            {"id": uuid.uuid4(), "t": t_b, "u": u_b, "w": wf_b},
        )
        await c.commit()
