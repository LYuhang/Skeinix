"""MCP T1 — FORCE RLS isolation + soft delete + partial unique indexes + CHECK.

Mirrors ``test_deployments_table_rls.py`` (Deployments T1). The
``app_engine`` fixture connects as the non-superuser ``vibecanvas_app``
role (the table owner); ``FORCE ROW LEVEL SECURITY`` binds even the
owner, so cross-tenant rows are invisible.
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


@pytest.mark.asyncio
async def test_mcp_servers_isolated_across_tenants(app_engine):
    """A mcp_servers row written under tenant A is invisible to tenant B."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    u_a = uuid.uuid4()

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
             "e": f"mcp-{uuid.uuid4().hex[:8]}@example.com"},
        )

    # Tenant A writes an mcp_servers row.
    row_id = uuid.uuid4()
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO mcp_servers "
                "(id, tenant_id, user_id, name, tool_prefix, transport, endpoint) "
                "VALUES (:id, :t, :u, 'Notion A', 'notion', 'sse', "
                "'https://example.com/sse')"
            ),
            {"id": row_id, "t": t_a, "u": u_a},
        )
        await c.commit()

    # Tenant B → must see zero.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_b)},
        )
        rows = (await c.execute(text("SELECT id FROM mcp_servers"))).all()
    assert rows == [], f"RLS leak — tenant B saw {rows}"

    # Tenant A → sees its own row.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        rows = (await c.execute(text("SELECT id FROM mcp_servers"))).all()
    assert [r[0] for r in rows] == [row_id]


@pytest.mark.asyncio
async def test_soft_delete_hides_row(app_engine):
    """Caller-side ``deleted_at IS NULL`` filter excludes soft-deleted rows."""
    t_a = uuid.uuid4()
    u_a = uuid.uuid4()
    await _seed_tenant_user(app_engine, t_a, u_a, "sd")

    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO mcp_servers "
                "(id, tenant_id, user_id, name, tool_prefix, transport, "
                "endpoint, deleted_at) "
                "VALUES (:id, :t, :u, 'Gone', 'gone', 'sse', "
                "'https://example.com', now())"
            ),
            {"id": uuid.uuid4(), "t": t_a, "u": u_a},
        )
        await c.commit()

        live = (await c.execute(text(
            "SELECT id FROM mcp_servers WHERE deleted_at IS NULL"
        ))).all()
        all_rows = (await c.execute(text(
            "SELECT id FROM mcp_servers"
        ))).all()
    assert live == [], "Soft-deleted rows must be filtered out by callers"
    assert len(all_rows) == 1, "But the row physically still exists"


@pytest.mark.asyncio
async def test_partial_unique_indexes(app_engine):
    """Two live rows in same tenant + same tool_prefix → second insert raises.

    Verifies the partial UNIQUE ix_mcp_servers_tenant_prefix.
    """
    t_a = uuid.uuid4()
    u_a = uuid.uuid4()
    await _seed_tenant_user(app_engine, t_a, u_a, "unq")

    # First row — succeeds.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        await c.execute(
            text(
                "INSERT INTO mcp_servers "
                "(id, tenant_id, user_id, name, tool_prefix, transport, endpoint) "
                "VALUES (:id, :t, :u, 'P1', 'same', 'sse', 'https://a.example.test')"
            ),
            {"id": uuid.uuid4(), "t": t_a, "u": u_a},
        )
        await c.commit()

    # Second live row with the same tool_prefix → partial UNIQUE violation.
    # Use a fresh connection so the IntegrityError rollback doesn't poison
    # earlier state.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        with pytest.raises(IntegrityError):
            await c.execute(
                text(
                    "INSERT INTO mcp_servers "
                    "(id, tenant_id, user_id, name, tool_prefix, transport, "
                    "endpoint) "
                    "VALUES (:id, :t, :u, 'P2', 'same', 'sse', 'https://b.example.test')"
                ),
                {"id": uuid.uuid4(), "t": t_a, "u": u_a},
            )
            await c.commit()


@pytest.mark.asyncio
async def test_prefix_format_check_rejects_bad_input(app_engine):
    """tool_prefix must match ``^[a-z][a-z0-9_]{0,30}$`` — uppercase / dash rejected."""
    t_a = uuid.uuid4()
    u_a = uuid.uuid4()
    await _seed_tenant_user(app_engine, t_a, u_a, "chk")

    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t_a)},
        )
        with pytest.raises(IntegrityError):
            await c.execute(
                text(
                    "INSERT INTO mcp_servers "
                    "(id, tenant_id, user_id, name, tool_prefix, transport, "
                    "endpoint) "
                    "VALUES (:id, :t, :u, 'Bad', 'Bad-Caps', 'sse', "
                    "'https://b.example.test')"
                ),
                {"id": uuid.uuid4(), "t": t_a, "u": u_a},
            )
            await c.commit()
