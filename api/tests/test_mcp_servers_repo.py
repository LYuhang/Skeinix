"""MCP T2 — ``McpServersRepo`` CRUD + soft-delete + conflict helper.

Coverage:

* ``insert`` + ``get`` round-trip writes and reads the same row.
* ``soft_delete`` makes ``get`` return ``None`` for the same id.
* ``list_other_tool_names(exclude_id=...)`` returns the prefixed names
  of EVERY OTHER live + enabled server in the tenant, and never the
  excluded server's own names.

Seeding follows the inline pattern Deployments T2 uses
(``test_deployments_repo_and_service.py``) rather than the plan-spec
``pg_session_factory`` + ``two_tenants_seed`` fixtures, which are not
present in this repo's conftest (MCP T1 inlined seeding too — same
choice carried forward).

We drive the repo through ``session_scope(tenant_id=...)`` so it runs
against the production-shape app role + RLS GUC, exactly as a route
handler would.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_mcp_servers import McpServersRepo


# --------------------------------------------------------------------- seed


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
    """Insert tenant + user via the RLS-bypassing superuser engine.
    Auth tables are RLS-free so a plain begin() block is fine."""
    async with pg_engine.begin() as c:
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
             "e": f"mcp-repo-{uuid.uuid4().hex[:6]}@example.com"},
        )


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_insert_get_roundtrip(pg_engine):
    """``insert`` writes a row whose ``get`` echoes name + prefix."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        sid = await repo.insert(
            tenant_id=tenant_id, user_id=user_id,
            name="Notion", tool_prefix="notion",
            transport="sse", endpoint="https://example.com/sse",
        )
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        row = await repo.get(sid)
    assert row is not None
    assert row["name"] == "Notion"
    assert row["tool_prefix"] == "notion"
    assert row["transport"] == "sse"
    assert row["endpoint"] == "https://example.com/sse"


@pytest.mark.asyncio
async def test_soft_delete_excludes_from_get(pg_engine):
    """``soft_delete`` flips ``deleted_at`` + ``enabled`` so ``get``
    returns ``None`` — the public read path can no longer see the row."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        sid = await repo.insert(
            tenant_id=tenant_id, user_id=user_id,
            name="Gone", tool_prefix="gone",
            transport="sse", endpoint="https://example.com",
        )
        await repo.soft_delete(sid)
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        assert (await repo.get(sid)) is None

    # Also confirm the row physically still exists and that soft_delete
    # also disabled it (so list_enabled would skip it).
    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                "SELECT enabled, deleted_at FROM mcp_servers WHERE id = :id"
            ),
            {"id": sid},
        )).one()
    assert row.enabled is False, (
        "soft_delete must also flip enabled=FALSE so the loader stops "
        "yielding this row"
    )
    assert row.deleted_at is not None


@pytest.mark.asyncio
async def test_update_handshake_encodes_tool_names(pg_engine):
    """``update_handshake`` must route through ``_encode_jsonb`` so a
    native Python ``list[dict]`` for ``tool_names`` is JSONB-encoded the
    same way ``insert`` / ``update`` do it — single source of truth.

    Round-trip: write via the helper, read back via ``get``, and assert
    asyncpg decoded the JSONB column back into a Python list of dicts
    (matching ``last_tool_names`` shape used by ``list_other_tool_names``)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        sid = await repo.insert(
            tenant_id=tenant_id, user_id=user_id,
            name="Handshake", tool_prefix="hs",
            transport="sse", endpoint="https://example.com/sse",
        )
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        await repo.update_handshake({
            sid: {
                "status": "ok",
                "tool_count": 2,
                "tool_names": [{"name": "a"}, {"name": "b"}],
            },
        })
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        row = await repo.get(sid)

    assert row is not None
    assert row["last_handshake_status"] == "ok"
    assert row["last_tool_count"] == 2
    assert row["last_handshake_at"] is not None
    names = row["last_tool_names"]
    assert isinstance(names, list)
    assert len(names) == 2
    assert all(isinstance(tn, dict) for tn in names), (
        "asyncpg should decode JSONB array-of-objects back to "
        "list[dict] — if this fails the helper double-encoded a string"
    )
    assert {tn["name"] for tn in names} == {"a", "b"}


@pytest.mark.asyncio
async def test_list_other_tool_names_excludes_self(pg_engine):
    """Two servers in the same tenant: when reading from S2's
    perspective (``exclude_id=s2``) the helper returns S1's prefixed
    names but never S2's own."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        await repo.insert(
            tenant_id=tenant_id, user_id=user_id,
            name="S1", tool_prefix="s1",
            transport="sse", endpoint="https://one.example.test",
            last_tool_names=[
                {"name": "create_page"}, {"name": "read_page"},
            ],
        )
        s2 = await repo.insert(
            tenant_id=tenant_id, user_id=user_id,
            name="S2", tool_prefix="s2",
            transport="sse", endpoint="https://two.example.test",
            last_tool_names=[{"name": "list"}],
        )
        await s.commit()

    async with session_scope(tenant_id=str(tenant_id)) as s:
        repo = McpServersRepo(s)
        names = await repo.list_other_tool_names(exclude_id=s2)

    assert "s1__create_page" in names
    assert "s1__read_page" in names
    assert "s2__list" not in names, (
        "list_other_tool_names must exclude the server identified by "
        "exclude_id"
    )
