"""MCP T5 — ``POST /api/v1/mcp-servers`` + ``POST /api/v1/mcp-servers/test``.

Strategy: handler-direct-call (mirrors Deployments T4
``test_deployments_create.py`` — the plan's TestClient path is fragile
against the ``current_user`` Bearer DI; handler-direct exercises the
same body validation + handshake + conflict pre-check + insert path
without standing up the auth stack).

Coverage (plan §842-970):

1. Reachable server → 201, response body is scrubbed of the bearer token.
2. Unreachable server → 201 anyway; ``last_handshake_status`` records the
   error so the UI can flag the row.
3. Built-in collision pre-check is bypassed when the prefixed name does
   not actually collide (``__`` separator makes overlap rare).
4. Same-prefix conflict: a second create with the same prefix in the
   same tenant → 409 via the partial UNIQUE on ``(tenant_id, tool_prefix)``.
5. Pydantic 422: token containing whitespace (newline) is rejected
   (CRLF-injection defense).
6. Pydantic 422: tool_prefix with uppercase / hyphens rejected
   (must match DB CHECK regex).
7. ``POST /test`` does NOT write to the DB (dry-run probe only).

Router-mount sanity is asserted via the built ASGI app's route list,
not via an end-to-end HTTP call.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.routes.mcp_servers import (
    CreateBody, create_mcp_server, dry_run_handshake,
)


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
             "e": f"mcp-t5-{uuid.uuid4().hex[:6]}@example.com"},
        )


class _StubCtx:
    """Lightweight stand-in for ``AuthContext``. The handler only reads
    ``tenant_id`` / ``user_id`` (both strings); ``email`` is unused."""

    def __init__(self, tenant_id, user_id):
        self.tenant_id = str(tenant_id)
        self.user_id = str(user_id)
        self.active_organization_id = str(tenant_id)
        self.session_id = "test-session"
        self.session_generation = 1
        self.membership_id = "test-membership"
        self.membership_role = "owner"
        self.membership_status = "active"
        self.authentication_strength = "password"
        self.email = "stub@example.com"


class _StubRequest:
    def __init__(self):
        self.headers = {}
        self.client = None
        self.state = SimpleNamespace(request_id="test-request")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")


def _body(**overrides) -> CreateBody:
    """Default well-formed create body. Overrides may supply alternate
    name / tool_prefix / endpoint / auth_config / transport."""
    base = {
        "name": "Notion",
        "tool_prefix": "notion",
        "transport": "sse",
        "endpoint": "https://example.com/sse",
        "auth_config": {"type": "bearer", "token": "tok-abc123"},
    }
    base.update(overrides)
    return CreateBody.model_validate(base)


def _fake_ok(tool_count: int, tool_names: list[dict]) -> dict:
    """``handshake_one`` success shape."""
    return {
        "status": "ok",
        "tool_count": tool_count,
        "tool_names": tool_names,
        "tools": [],  # routes never read this; only the loader does
    }


def _fake_error(msg: str) -> dict:
    """``handshake_one`` failure shape."""
    return {
        "status": f"error: {msg}",
        "tool_count": None,
        "tool_names": None,
        "tools": [],
    }


# ---------------------------------------------------------------- 1. reachable


@pytest.mark.asyncio
async def test_create_with_reachable_server_returns_201(pg_engine):
    """Reachable handshake → 201 with scrubbed body + persisted snapshot.

    Critical secret-handling invariant: the raw bearer token MUST NOT
    appear anywhere in the response body. The handler scrubs it to
    ``"***"`` before returning the row.
    """
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)
    body = _body()

    handshake_result = _fake_ok(
        tool_count=2,
        tool_names=[
            {"name": "create_page", "description": "create a page"},
            {"name": "search", "description": "search pages"},
        ],
    )
    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        return_value=handshake_result,
    ):
        async with session_scope(tenant_id=str(tenant_id)) as s:
            resp = await create_mcp_server(
                body=body, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

    assert resp["tool_prefix"] == "notion"
    assert resp["last_handshake_status"] == "ok"
    assert resp["last_tool_count"] == 2
    # G4b — bearer token MUST be scrubbed.
    assert "tok-abc123" not in str(resp), (
        f"raw bearer token leaked in response: {resp}"
    )
    # The scrubbed marker IS present.
    assert resp["auth_config"]["token"] == "***"

    # Persisted snapshot fields.
    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                "SELECT tenant_id, last_handshake_status, last_tool_count, "
                "       last_tool_names, last_handshake_at, enabled "
                "FROM mcp_servers WHERE id = :id"
            ),
            {"id": uuid.UUID(resp["id"])},
        )).one()
    assert row.tenant_id == tenant_id     # came from ctx, not body
    assert row.last_handshake_status == "ok"
    assert row.last_tool_count == 2
    assert row.last_handshake_at is not None
    assert row.enabled is True


# -------------------------------------------------------------- 2. unreachable


@pytest.mark.asyncio
async def test_create_with_unreachable_server_still_returns_201(pg_engine):
    """Handshake timeout → still 201; row records the error status so the
    UI can flag the unreachable server. The operator can PATCH the
    endpoint and trigger a refresh later (MCP T6)."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)
    body = _body(name="Unreachable", tool_prefix="dead",
                 endpoint="https://does-not-resolve.invalid/sse")

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        return_value=_fake_error("handshake timed out after 10.0s"),
    ):
        async with session_scope(tenant_id=str(tenant_id)) as s:
            resp = await create_mcp_server(
                body=body, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

    assert resp["last_handshake_status"].startswith("error:")
    # Unreachable → no tool snapshot.
    assert resp["last_tool_count"] is None
    assert resp["last_tool_names"] is None


# ----------------------------------------------------- 3. no-builtin-collision


@pytest.mark.asyncio
async def test_create_conflict_with_builtin(pg_engine):
    """An MCP tool whose bare name matches a built-in (``get_workflow``) but
    whose PREFIXED form is ``x__get_workflow`` does NOT collide — built-in
    names never contain ``__``. The create still succeeds with 201."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)
    body = _body(name="Mimic", tool_prefix="x")

    handshake_result = _fake_ok(
        tool_count=1,
        # Bare name "get_workflow" matches a real built-in by string
        # equality. But the prefix invariant means it gets stored as
        # "x__get_workflow", which does NOT collide.
        tool_names=[
            {"name": "get_workflow", "description": "shadow"},
        ],
    )
    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        return_value=handshake_result,
    ):
        async with session_scope(tenant_id=str(tenant_id)) as s:
            resp = await create_mcp_server(
                body=body, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

    assert resp["tool_prefix"] == "x"
    assert resp["last_handshake_status"] == "ok"


# ---------------------------------------------------- 4. same-prefix collision


@pytest.mark.asyncio
async def test_create_conflict_with_other_server(pg_engine):
    """A second create with the same ``tool_prefix`` in the same tenant
    hits the partial UNIQUE on ``(tenant_id, tool_prefix) WHERE
    deleted_at IS NULL`` (migration 006) → 409."""
    from fastapi import HTTPException

    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)
    body = _body(name="First", tool_prefix="dup")
    # Different name for the second body — only the prefix needs to
    # collide. (Using the same name would surface the OTHER partial
    # UNIQUE; we're explicitly exercising the prefix one.)
    body2 = _body(name="Second", tool_prefix="dup")

    handshake_result = _fake_ok(
        tool_count=1,
        tool_names=[{"name": "ping", "description": "p"}],
    )
    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        return_value=handshake_result,
    ):
        async with session_scope(tenant_id=str(tenant_id)) as s:
            await create_mcp_server(
                body=body, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

        # Second insert with same prefix under same tenant — must 409.
        with pytest.raises(HTTPException) as exc_info:
            async with session_scope(tenant_id=str(tenant_id)) as s:
                await create_mcp_server(
                    body=body2, request=_StubRequest(), ctx=ctx, session=s,
                    service=_AllowAuthz(),
                )
                await s.commit()

    assert exc_info.value.status_code == 409


# ------------------------ 4b. cross-server prefixed-name conflict pre-check


@pytest.mark.asyncio
async def test_create_conflict_with_other_server_tool_name_overlap(
    pg_engine, monkeypatch,
):
    """Cross-server prefixed-name collision (``{prefix}__{name}`` exists on
    another live + enabled server) → 409 via the ``list_other_tool_names``
    pre-check.

    Distinct from test #4 (same-prefix DB partial-UNIQUE collision):
    this exercises the SOFT pre-check at routes ``mcp_servers.py:299-308``
    — the new server's prefix is unique (no DB unique violation), but its
    prefixed tool name overlaps with a name already exported by another
    server in the same tenant. Without this guard the row would insert
    successfully, and the loader would silently drop one of the tools at
    agent build time (whichever loses the dedupe race).

    Test design: patch ``McpServersRepo.list_other_tool_names`` to return
    a fixed set containing ``newprefix__foo``. The new body has prefix
    ``newprefix`` and handshake returns a tool named ``foo`` — so the
    pre-check constructs ``newprefix__foo``, finds it in ``other_names``,
    and raises 409. Patching the repo method directly (not seeding a real
    row + relying on RLS) keeps the test surgical: we're explicitly
    testing the pre-check branch, not the SQL it runs.
    """
    from fastapi import HTTPException

    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    # Patch list_other_tool_names so the pre-check sees an existing
    # cross-server prefixed name to collide with. The method is async
    # → AsyncMock-style coroutine.
    async def fake_list_other(self, exclude_id=None):
        return {"newprefix__foo"}

    monkeypatch.setattr(
        "vibecanvas_api.storage.repo_mcp_servers."
        "McpServersRepo.list_other_tool_names",
        fake_list_other,
    )

    ctx = _StubCtx(tenant_id, user_id)
    # Different prefix from anything else → no DB partial-UNIQUE will
    # fire; the ONLY way this 409s is via the pre-check branch.
    body = _body(name="New", tool_prefix="newprefix",
                 auth_config={"type": "none"})

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        new_callable=AsyncMock,
    ) as mock_hs:
        mock_hs.return_value = _fake_ok(
            tool_count=1,
            tool_names=[{"name": "foo", "description": "x"}],
        )
        async with session_scope(tenant_id=str(tenant_id)) as s:
            with pytest.raises(HTTPException) as exc_info:
                await create_mcp_server(
                    body=body, request=_StubRequest(), ctx=ctx, session=s,
                    service=_AllowAuthz(),
                )

    assert exc_info.value.status_code == 409
    # The error message names the specific colliding prefixed-name so
    # the operator can find it in the Settings UI.
    detail = str(exc_info.value.detail).lower()
    assert "newprefix__foo" in detail or "another mcp server" in detail


# ------------------------------------------------------ 5. invalid token shape


def test_create_invalid_token_format():
    """Bearer tokens containing whitespace (newline) → Pydantic 422. The
    constraint is anti-CRLF-injection: a newline in the token would let
    a malicious paste smuggle an extra HTTP header."""
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        CreateBody.model_validate({
            "name": "X",
            "tool_prefix": "x",
            "transport": "sse",
            "endpoint": "https://example.com/sse",
            "auth_config": {"type": "bearer", "token": "has\nnewline"},
        })


# ----------------------------------------------------- 6. invalid prefix shape


def test_create_invalid_prefix_format():
    """``tool_prefix`` must match ``^[a-z][a-z0-9_]{0,30}$``. Uppercase
    / hyphens are rejected with 422 BEFORE the DB CHECK fires (friendlier
    error path)."""
    import pydantic
    with pytest.raises(pydantic.ValidationError):
        CreateBody.model_validate({
            "name": "X",
            "tool_prefix": "BAD-CAPS",
            "transport": "sse",
            "endpoint": "https://example.com/sse",
            "auth_config": {"type": "none"},
        })


# -------------------------------------------------------------- 7. dry-run /test


@pytest.mark.asyncio
async def test_test_dry_run_does_not_write_db(pg_engine):
    """``POST /test`` is a probe: it returns the tool snapshot but writes
    NOTHING to ``mcp_servers``. The row count before and after the call
    is identical."""

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)
    body = _body(name="ProbeTarget", tool_prefix="probe")

    async with pg_engine.connect() as c:
        before = (await c.execute(
            text("SELECT COUNT(*) FROM mcp_servers")
        )).scalar()

    handshake_result = _fake_ok(
        tool_count=3,
        tool_names=[
            {"name": "a", "description": "a"},
            {"name": "b", "description": "b"},
            {"name": "c", "description": "c"},
        ],
    )
    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        return_value=handshake_result,
    ):
        resp = await dry_run_handshake(
            body=body, request=_StubRequest(), ctx=ctx,
            service=_AllowAuthz(),
        )

    assert resp == {
        "ok": True,
        "tool_count": 3,
        "tool_names": [
            {"name": "a", "description": "a"},
            {"name": "b", "description": "b"},
            {"name": "c", "description": "c"},
        ],
    }

    async with pg_engine.connect() as c:
        after = (await c.execute(
            text("SELECT COUNT(*) FROM mcp_servers")
        )).scalar()
    assert after == before, (
        "POST /test must not write to mcp_servers (dry-run probe)"
    )


# ----------------------------------------------------------- router mount


def test_router_mounted_under_api_v1():
    """The mcp-servers router is registered under ``/api/v1/mcp-servers``
    AND ``/test`` is registered BEFORE any future ``/{server_id}`` route
    so FastAPI's path-matcher dispatches the literal string."""
    from vibecanvas_api.app import build_app
    from vibecanvas_api.authorization.manifest import application_route_contexts

    app = build_app()
    paths = [r.path for r in application_route_contexts(app)]
    assert "/api/v1/mcp-servers" in paths
    assert "/api/v1/mcp-servers/test" in paths
