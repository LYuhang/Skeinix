"""MCP T6 — GET / PATCH / DELETE / refresh routes.

Strategy: handler-direct-call + ``_StubCtx`` (mirrors MCP T5
``test_mcp_servers_create.py`` and Deployments T4 — TestClient over the
real ``current_user`` Bearer DI is fragile in this repo; handler-direct
exercises the exact same body validation + repo + handshake paths
without standing up the auth stack).

Coverage (plan §1158-1235):

1. ``test_list_then_get`` — seed a row, list returns it under ``items``,
   GET by id returns the same row.
2. ``test_patch_toggle_enabled`` — PATCH ``{"enabled": False}`` flips it,
   then PATCH ``{"enabled": True}`` flips it back.
3. ``test_patch_rejects_immutable_fields`` — PATCH ``{"transport": ...}``
   and ``{"tool_prefix": ...}`` both 422 via Pydantic
   ``extra='forbid'``. Defense-in-depth against a client that tries to
   smuggle identity-shifting columns.
4. ``test_patch_endpoint_re_handshakes_and_re_checks_conflicts`` —
   PATCH ``endpoint`` triggers a re-handshake; mock the conflict helper
   so the new tool name overlaps another server's existing prefixed
   name → 409.
5. ``test_delete_returns_204_and_hides_row`` — DELETE returns 204; the
   row is gone (GET → 404).
6. ``test_refresh_updates_last_handshake_status`` — POST ``/refresh``
   re-probes without changing config; the snapshot columns reflect the
   new probe.

Two extra defensive tests beyond the plan minimum:

7. ``test_get_missing_returns_404`` — explicit 404 path for the
   single-row GET.
8. ``test_patch_endpoint_re_handshakes_and_succeeds`` — happy path of
   the re-handshake branch: PATCH endpoint, no conflict → 200 with
   refreshed ``last_handshake_*``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.routes.mcp_servers import (
    PatchBody,
    delete_mcp_server,
    get_mcp_server,
    list_mcp_servers,
    patch_mcp_server,
    refresh_mcp_server,
)


# ------------------------------------------------------------------- helpers


async def _seed_tenant_and_user(pg_engine, tenant_id, user_id) -> None:
    """Insert tenant + user via the RLS-bypassing superuser engine.
    Auth tables are RLS-free so a plain ``begin()`` block is fine.
    Same inline helper as ``test_mcp_servers_create.py`` — copied (not
    extracted) so each MCP test file remains self-contained."""
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
             "e": f"mcp-t6-{uuid.uuid4().hex[:6]}@example.com"},
        )
        await c.execute(
            text(
                "INSERT INTO organizations("
                "tenant_id, kind, slug, name, created_by"
                ") VALUES (:t, 'personal', :slug, 'Test account', :u)"
            ),
            {
                "t": tenant_id,
                "u": user_id,
                "slug": f"test-{tenant_id.hex}",
            },
        )


async def _seed_mcp_server(
    pg_engine,
    tenant_id,
    user_id,
    *,
    name: str = "Existing",
    tool_prefix: str = "existing",
    endpoint: str = "https://example.com/sse",
    auth_config: dict | None = None,
    tool_names: list[dict] | None = None,
    enabled: bool = True,
) -> uuid.UUID:
    """Insert a current-schema MCP row with SecretService-backed auth.
    Returns the row's UUID. Caller uses this id directly to drive the
    handler-under-test.

    Default snapshot is ``last_handshake_status='ok'`` with the supplied
    tool names — that way a re-handshake conflict test can rely on
    ``list_other_tool_names`` seeing the row.
    """
    sid = uuid.uuid4()
    auth = auth_config if auth_config is not None else {
        "type": "bearer", "token": "tok-seeded",
    }
    names = tool_names if tool_names is not None else []
    from vibecanvas_api.security.secret_service import secret_service
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.repo_mcp_servers import McpServersRepo

    async with session_scope(tenant_id=str(tenant_id)) as session:
        stored_auth = {"type": auth.get("type", "none")}
        secret_ref = None
        if stored_auth["type"] == "bearer":
            secret_ref = await secret_service().put_text(
                session,
                tenant_id=tenant_id,
                purpose="mcp_bearer_token",
                resource_type="mcp_installation",
                resource_id=sid,
                plaintext=str(auth["token"]),
            )
        await McpServersRepo(session).insert(
            id=sid,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            tool_prefix=tool_prefix,
            transport="sse",
            endpoint=endpoint,
            auth_config=stored_auth,
            auth_secret_ref=secret_ref,
            auth_secret_version=1,
            connection_config={},
            connection_secret_version=1,
            enabled=enabled,
            last_handshake_status="ok",
            last_tool_count=len(names),
            last_tool_names=names,
            last_handshake_at=datetime.now(timezone.utc),
        )
    return sid


class _StubCtx:
    """Lightweight stand-in for ``AuthContext``. The handlers only read
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
    def __init__(self, resource_ids=()):
        self._resource_ids = tuple(str(value) for value in resource_ids)

    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")

    async def list_authorized_ids(self, *args, **kwargs):
        return self._resource_ids

    async def batch_check(self, checks):
        return tuple(
            Decision(allowed=True, reason_code="test_fixture")
            for _ in checks
        )


def _fake_ok(tool_count: int, tool_names: list[dict]) -> dict:
    """``handshake_one`` success shape."""
    return {
        "status": "ok",
        "tool_count": tool_count,
        "tool_names": tool_names,
        "tools": [],
    }


# ------------------------------------------------------------- 1. list + get


@pytest.mark.asyncio
async def test_list_then_get(pg_engine):
    """Seed a row → list contains it under ``items``; GET by id returns
    the same row with secrets scrubbed."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    sid = await _seed_mcp_server(pg_engine, tenant_id, user_id)

    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        listing = await list_mcp_servers(
            request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz((sid,)),
        )
        single = await get_mcp_server(
            server_id=sid, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )

    assert "items" in listing
    ids = {item["id"] for item in listing["items"]}
    assert str(sid) in ids
    assert single["id"] == str(sid)
    # Token must be scrubbed on the way out.
    assert single["auth_config"]["token"] == "***"
    assert "tok-seeded" not in str(single)


@pytest.mark.asyncio
async def test_get_missing_returns_404(pg_engine):
    """Unknown server_id → 404. Same response shape regardless of
    whether the id is wrong, soft-deleted, or in another tenant
    (RLS hides the latter equivalently to non-existence)."""
    from fastapi import HTTPException

    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        with pytest.raises(HTTPException) as exc:
            await get_mcp_server(
                server_id=uuid.uuid4(), request=_StubRequest(), ctx=ctx,
                session=s, service=_AllowAuthz(),
            )
    assert exc.value.status_code == 404


# -------------------------------------------------------- 2. patch enabled


@pytest.mark.asyncio
async def test_patch_toggle_enabled(pg_engine):
    """PATCH ``{"enabled": False}`` then ``{"enabled": True}`` — the
    repo update + outbound scrub correctly reflect the flip without
    a re-handshake (endpoint / auth_config not in the body)."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    sid = await _seed_mcp_server(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        # Note: PatchBody validation is normally done by FastAPI before
        # the handler runs; we call .model_validate() explicitly here.
        body = PatchBody.model_validate({"enabled": False})
        r1 = await patch_mcp_server(
            server_id=sid, body=body, request=_StubRequest(), ctx=ctx,
            session=s, service=_AllowAuthz(),
        )
        await s.commit()

    assert r1["enabled"] is False

    async with session_scope(tenant_id=str(tenant_id)) as s:
        body = PatchBody.model_validate({"enabled": True})
        r2 = await patch_mcp_server(
            server_id=sid, body=body, request=_StubRequest(), ctx=ctx,
            session=s, service=_AllowAuthz(),
        )
        await s.commit()

    assert r2["enabled"] is True


# --------------------------------------------- 3. patch rejects immutable


def test_patch_rejects_immutable_fields():
    """``PatchBody`` is ``extra='forbid'`` — any attempt to PATCH
    ``transport`` or ``tool_prefix`` (immutable identifiers) is rejected
    by Pydantic with a validation error BEFORE the handler runs. FastAPI
    surfaces this as 422 to the wire; we assert the ValidationError
    here because we're calling validation directly."""
    import pydantic

    for field in ("transport", "tool_prefix"):
        with pytest.raises(pydantic.ValidationError):
            PatchBody.model_validate({field: "new_value"})


# ------------------------------------ 4. patch endpoint re-handshakes (409)


@pytest.mark.asyncio
async def test_patch_endpoint_re_handshakes_and_re_checks_conflicts(
    pg_engine, monkeypatch,
):
    """PATCH ``endpoint`` → handler re-handshakes with the new endpoint
    and re-runs the cross-server conflict pre-check. If the new tool
    name collides with another server's prefixed name → 409.

    Test design (mirrors the T5 amend test
    ``test_create_conflict_with_other_server_tool_name_overlap``):
    monkeypatch ``McpServersRepo.list_other_tool_names`` to return a
    fixed set containing the would-be-prefixed name. This is surgical —
    we're testing the conflict-detection BRANCH, not the SQL behind
    ``list_other_tool_names`` (covered by repo unit tests).
    """
    from fastapi import HTTPException

    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    sid = await _seed_mcp_server(
        pg_engine, tenant_id, user_id,
        name="Patched", tool_prefix="patched",
    )
    ctx = _StubCtx(tenant_id, user_id)

    # Pretend another server in the tenant already exports
    # ``patched__create_page``. exclude_id=sid is asserted indirectly:
    # the fake ignores it and always returns the colliding set.
    captured_exclude = []

    async def fake_list_other(self, exclude_id=None):
        captured_exclude.append(exclude_id)
        return {"patched__create_page"}

    monkeypatch.setattr(
        "vibecanvas_api.storage.repo_mcp_servers."
        "McpServersRepo.list_other_tool_names",
        fake_list_other,
    )

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        new_callable=AsyncMock,
    ) as mock_hs:
        mock_hs.return_value = _fake_ok(
            tool_count=1,
            tool_names=[{"name": "create_page", "description": "p"}],
        )
        async with session_scope(tenant_id=str(tenant_id)) as s:
            body = PatchBody.model_validate(
                {"endpoint": "https://new.example.test/sse"},
            )
            with pytest.raises(HTTPException) as exc:
                await patch_mcp_server(
                    server_id=sid, body=body, request=_StubRequest(), ctx=ctx,
                    session=s, service=_AllowAuthz(),
                )

    assert exc.value.status_code == 409
    # LOAD-BEARING: exclude_id MUST be the row we're patching, or our
    # own previous tool_names would be in the comparison set and even
    # an unchanged tool would falsely 409 on a no-op re-handshake.
    assert captured_exclude == [sid], (
        f"list_other_tool_names must be called with exclude_id={sid!s}, "
        f"got {captured_exclude}"
    )


@pytest.mark.asyncio
async def test_patch_endpoint_re_handshakes_and_succeeds(pg_engine):
    """PATCH ``endpoint`` with no conflict → 200; the
    ``last_handshake_*`` columns reflect the new probe. Validates the
    happy path of the re-handshake branch beyond the conflict case."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    sid = await _seed_mcp_server(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        new_callable=AsyncMock,
    ) as mock_hs:
        mock_hs.return_value = _fake_ok(
            tool_count=2,
            tool_names=[
                {"name": "new_a", "description": "a"},
                {"name": "new_b", "description": "b"},
            ],
        )
        async with session_scope(tenant_id=str(tenant_id)) as s:
            body = PatchBody.model_validate(
                {"endpoint": "https://new.example.com/sse"},
            )
            resp = await patch_mcp_server(
                server_id=sid, body=body, request=_StubRequest(), ctx=ctx,
                session=s, service=_AllowAuthz(),
            )
            await s.commit()

    assert resp["endpoint"] == "https://new.example.com/sse"
    assert resp["last_tool_count"] == 2
    assert resp["last_handshake_status"] == "ok"
    # Persisted check: last_handshake_at moves forward.
    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                "SELECT last_tool_count, last_handshake_status "
                "FROM mcp_servers WHERE id = :id"
            ),
            {"id": sid},
        )).one()
    assert row.last_tool_count == 2
    assert row.last_handshake_status == "ok"


# ----------------------------------------------------------- 5. delete (204)


@pytest.mark.asyncio
async def test_delete_returns_204_and_hides_row(pg_engine):
    """DELETE → 204 No Content; subsequent GET → 404 (the row is soft-
    deleted; the repo's WHERE filters it out)."""
    from fastapi import HTTPException

    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    sid = await _seed_mcp_server(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    async with session_scope(tenant_id=str(tenant_id)) as s:
        resp = await delete_mcp_server(
            server_id=sid, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.commit()
    assert resp.status_code == 204

    async with session_scope(tenant_id=str(tenant_id)) as s:
        with pytest.raises(HTTPException) as exc:
            await get_mcp_server(
                server_id=sid, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
    assert exc.value.status_code == 404

    # And ``enabled`` is FALSE so the loader's enabled-only scan stops
    # yielding it immediately, even before any future deleted_at filter
    # is added.
    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                "SELECT enabled, deleted_at FROM mcp_servers "
                "WHERE id = :id"
            ),
            {"id": sid},
        )).one()
    assert row.enabled is False
    assert row.deleted_at is not None


# ------------------------------------------------------ 6. refresh re-probe


@pytest.mark.asyncio
async def test_refresh_updates_last_handshake_status(pg_engine):
    """POST ``/refresh`` re-probes the existing config and writes the
    fresh snapshot. ``handshake_one`` is mocked to return 5 tools;
    the row's ``last_tool_count`` reflects that exactly."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    sid = await _seed_mcp_server(pg_engine, tenant_id, user_id)
    ctx = _StubCtx(tenant_id, user_id)

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        new_callable=AsyncMock,
    ) as mock_hs:
        mock_hs.return_value = _fake_ok(
            tool_count=5,
            tool_names=[{"name": f"t{i}", "description": "x"} for i in range(5)],
        )
        async with session_scope(tenant_id=str(tenant_id)) as s:
            resp = await refresh_mcp_server(
                server_id=sid, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

    assert resp["last_tool_count"] == 5
    assert resp["last_handshake_status"] == "ok"
    assert len(resp["last_tool_names"]) == 5


# ----------------------------------------------- 7. refresh persists errors


@pytest.mark.asyncio
async def test_refresh_writes_error_status_on_handshake_failure(pg_engine):
    """POST ``/refresh`` writes the error status even when the probe
    fails — the UI needs to see "unreachable" badges after a transient
    outage, not stale ``ok`` from the previous run. Mock
    ``handshake_one`` to return a ``status='error: timeout'`` shape;
    assert the row's ``last_handshake_status`` is updated to the error
    string and ``last_tool_count`` is overwritten with ``None``
    (otherwise the badge would show stale tool count next to "error")."""
    from vibecanvas_api.storage.db import session_scope

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    await _seed_tenant_and_user(pg_engine, tenant_id, user_id)
    # Seed with a successful snapshot — so the test proves the error
    # branch OVERWRITES the previous ok state (not merely fills in NULLs).
    sid = await _seed_mcp_server(
        pg_engine, tenant_id, user_id,
        tool_names=[{"name": "t0", "description": "x"}],
    )
    ctx = _StubCtx(tenant_id, user_id)

    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one",
        new_callable=AsyncMock,
    ) as mock_hs:
        mock_hs.return_value = {
            "status": "error: timeout",
            "tool_count": None,
            "tool_names": None,
            "tools": [],
        }
        async with session_scope(tenant_id=str(tenant_id)) as s:
            resp = await refresh_mcp_server(
                server_id=sid, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

    assert resp["last_handshake_status"].startswith("error:")
    assert resp["last_tool_count"] is None

    # Persisted check — the prior tool_count (1) was overwritten with
    # NULL, not retained.
    async with pg_engine.connect() as c:
        row = (await c.execute(
            text(
                "SELECT last_tool_count, last_handshake_status "
                "FROM mcp_servers WHERE id = :id"
            ),
            {"id": sid},
        )).one()
    assert row.last_tool_count is None
    assert row.last_handshake_status.startswith("error:")


# -------------------------------------------- 8. patch rejects null auth_config


def test_patch_rejects_null_auth_config():
    """``PatchBody.auth_config: Optional[AuthConfig] = None`` is the
    UNSET sentinel — clients OMIT the field to leave auth unchanged.
    An explicit ``{"auth_config": null}`` is meaningless (there is no
    "no auth_config" state; use ``{"type":"none"}`` for no-auth). Without
    a validator, the explicit null would slip past Pydantic and the
    handler would invoke ``handshake_one(auth_config=None)``, which
    eventually attempts ``_headers(None).get(...)`` → AttributeError
    → 500 ISE. The field validator surfaces 422 at the boundary.

    Sibling fields (``name``, ``endpoint``, ``enabled``) explicitly
    ARE allowed to remain unset (omitted from the body); this test
    only asserts the null-rejection for ``auth_config`` and proves
    that omitting it still validates cleanly."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        PatchBody.model_validate({"auth_config": None})

    # Sanity: an empty body still validates — omission is the unset
    # path that ``exclude_unset=True`` keys off of.
    PatchBody.model_validate({})
