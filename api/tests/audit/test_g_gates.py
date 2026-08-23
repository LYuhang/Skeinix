"""Consolidated audit-log verification gates.

This is a SINGLE gate suite that re-asserts the security-critical invariants
of the audit log. It deliberately overlaps with the per-task tests
(``test_audit_schema.py`` G1/G2, ``test_audit_service.py`` G3/G4,
``test_audit_auth_hooks.py`` G4/G5, ``test_audit_resource_hooks.py`` G5/G7,
``test_audit_read_api.py`` G6) — the point is one place that proves the whole
contract. The fixture / role / seed mechanics mirror those files exactly:

* ``app_engine``     — the non-superuser ``vibecanvas_app`` role (table owner);
                       FORCE RLS binds even the owner. Append-only + RLS gates
                       must assert as THIS role (a superuser would bypass both).
* ``pg_engine``      — the superuser engine: bypasses RLS + ownership. Used to
                       seed NULL-tenant / cross-tenant rows AND (G1 enhancement)
                       to prove the append-only trigger fires even for a role
                       with every privilege.
* ``admin_engine``   — ``pg_engine`` injected as ``db._admin_engine`` so
                       ``record_auth_audit`` (``session_scope_admin``) can write
                       the explicit NULL-tenant auth row.

``audit_log`` is NOT in conftest's truncate list, so every read-back is scoped
by a freshly-minted ``tenant_id`` / unique marker email.

# G1 append-only: UPDATE + DELETE raise — as vibecanvas_app AND as superuser
#                 (the trigger, not the grant/RLS, is the real guard).
# G2 RLS isolation: tenant B can't see tenant A; NULL-tenant row hidden.
# G3 atomic resource write: rollback → zero rows.
# G4 auth-failure trail: unknown-email → tenant_id IS NULL (via admin engine).
# G5 no-secrets: scan every column + meta::text for the known secret literals.
# G6 read API: tenant-scoped + action/outcome/from/to filters + cursor stable.
# G7 all 14 hooks fire: parametrized over the action taxonomy, each → a row.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from vibecanvas_api.audit import actions
from vibecanvas_api.audit.service import record_audit, record_auth_audit
from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.security.secret_service import secret_service
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_mcp_servers import McpServersRepo

# The exact text the append-only trigger RAISEs (migration 009 ll.74).
APPEND_ONLY_MSG = "audit_log is append-only (no UPDATE/DELETE)"


# ---------------------------------------------------------------------------
# Shared fixtures / helpers (mirror the per-task test mechanics).
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def admin_engine(monkeypatch, pg_engine):
    """Inject the superuser ``pg_engine`` as ``db._admin_engine`` so
    ``record_auth_audit`` (via ``session_scope_admin``) runs RLS-bypassing and
    the explicit NULL-tenant INSERT is permitted. Mirrors
    ``test_audit_service.py`` / ``test_audit_auth_hooks.py``."""
    from vibecanvas_api.storage import db as db_mod

    monkeypatch.setattr(db_mod, "_admin_engine", pg_engine)
    yield pg_engine


async def _seed_tenant(engine, tid):
    async with engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tid},
        )


async def _seed_tenant_user(pg_engine):
    """Tenant + user via the superuser engine (auth tables are RLS-free)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tenant_id},
        )
        await c.execute(
            text("INSERT INTO users(user_id, tenant_id, email) VALUES (:u, :t, :e)"),
            {"u": user_id, "t": tenant_id, "e": f"gg-{uuid.uuid4().hex[:6]}@example.com"},
        )
        await c.execute(
            text(
                "INSERT INTO organizations(tenant_id, kind, slug, name, created_by) "
                "VALUES (:t, 'personal', :slug, 'Audit account', :u)"
            ),
            {"t": tenant_id, "u": user_id, "slug": f"audit-{tenant_id.hex}"},
        )
    return tenant_id, user_id


async def _seed_audit_row(
    pg_engine, tenant_id, *, action, outcome="success", created_at, target_name=None
):
    """One audit row via the superuser engine (explicit tenant_id + created_at
    for a deterministic keyset order)."""
    aid = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO audit_log "
                "(audit_id, tenant_id, action, outcome, target_name, created_at) "
                "VALUES (:a, :t, :act, :o, :tn, :ts)"
            ),
            {
                "a": aid,
                "t": tenant_id,
                "act": action,
                "o": outcome,
                "tn": target_name,
                "ts": created_at,
            },
        )
    return aid


async def _audit_rows(pg_engine, tenant_id):
    async with pg_engine.connect() as c:
        res = await c.execute(
            text(
                "SELECT action, actor_user_id::text, actor_email, target_type, "
                "       target_id, target_name, outcome, ip_address, user_agent, "
                "       tenant_id::text, request_id, meta::text "
                "FROM audit_log WHERE tenant_id = :t ORDER BY created_at"
            ),
            {"t": tenant_id},
        )
        return list(res.mappings())


async def _audit_blob(pg_engine, tenant_id):
    rows = await _audit_rows(pg_engine, tenant_id)
    return "\n".join(
        "|".join("" if v is None else str(v) for v in r.values()) for r in rows
    )


# ===========================================================================
# G1 — append-only: UPDATE + DELETE raise (vibecanvas_app AND superuser).
# ===========================================================================
@pytest.mark.asyncio
async def test_g1_append_only_blocks_update_and_delete(app_engine, pg_engine):
    """G1: the append-only BEFORE UPDATE OR DELETE trigger is the load-bearing
    guard. Assert via BOTH roles:

    * ``vibecanvas_app`` (owner; FORCE RLS + REVOKE apply) — UPDATE/DELETE raise.
    * ``pg_engine`` SUPERUSER — has every privilege AND bypasses RLS, so neither
      the grant nor RLS can be the guard here. The UPDATE/DELETE must STILL
      raise with the trigger's exact message — proving the TRIGGER (not the
      grant/RLS) enforces append-only. (T1 reviewer finding.)
    """
    tid = uuid.uuid4()
    await _seed_tenant(app_engine, tid)

    # Seed one row as vibecanvas_app (tenant_id auto-fills from the GUC).
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tid)}
        )
        await c.execute(
            text(
                "INSERT INTO audit_log (action, outcome) "
                "VALUES ('auth.logout', 'success')"
            )
        )
        await c.commit()

    # --- vibecanvas_app path: UPDATE / DELETE both raise.
    #     NOTE: as the app role the REVOKE grant trips FIRST ("permission denied
    #     for table audit_log") — the statement never reaches the trigger, so we
    #     only assert it RAISES here. The trigger-message assertion lives in the
    #     superuser path below (where no grant/RLS can be the guard). This split
    #     is precisely the T1 reviewer finding: the grant masks the trigger for
    #     the app role, so the trigger must be proven independently.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tid)}
        )
        with pytest.raises(Exception):
            await c.execute(text("UPDATE audit_log SET outcome='failure'"))

    # --- vibecanvas_app path: DELETE raises.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tid)}
        )
        with pytest.raises(Exception):
            await c.execute(text("DELETE FROM audit_log"))

    # --- SUPERUSER path (the enhancement): grant + RLS are NOT the guard here.
    #     UPDATE must still raise the TRIGGER's message.
    async with pg_engine.connect() as c:
        with pytest.raises(Exception) as ei:
            await c.execute(
                text("UPDATE audit_log SET outcome='failure' WHERE tenant_id=:t"),
                {"t": tid},
            )
        assert APPEND_ONLY_MSG in str(ei.value), (
            "superuser UPDATE did not hit the append-only trigger — the trigger "
            "is NOT the real guard"
        )

    # --- SUPERUSER path: DELETE must still raise the TRIGGER's message.
    async with pg_engine.connect() as c:
        with pytest.raises(Exception) as ei:
            await c.execute(
                text("DELETE FROM audit_log WHERE tenant_id=:t"), {"t": tid}
            )
        assert APPEND_ONLY_MSG in str(ei.value), (
            "superuser DELETE did not hit the append-only trigger — the trigger "
            "is NOT the real guard"
        )

    # And the row is still there (nothing was mutated/removed).
    async with pg_engine.connect() as c:
        n = (
            await c.execute(
                text("SELECT count(*) FROM audit_log WHERE tenant_id=:t"), {"t": tid}
            )
        ).scalar()
    assert n == 1


@pytest.mark.asyncio
async def test_g1_app_cannot_forge_account_erasure_override(app_engine):
    """The custom erasure GUC is not an authorization credential.

    PostgreSQL clients may set arbitrary custom GUCs, so the append-only
    trigger must also require the dedicated maintenance database identity.
    """
    tid = uuid.uuid4()
    await _seed_tenant(app_engine, tid)
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tid)},
        )
        await c.execute(
            text(
                "INSERT INTO audit_log (action, outcome) "
                "VALUES ('auth.logout', 'success')"
            )
        )
        await c.commit()

    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tid)},
        )
        await c.execute(text("SET LOCAL app.account_erasure = 'on'"))
        with pytest.raises(Exception) as exc_info:
            await c.execute(
                text("UPDATE audit_log SET meta = '{}'::jsonb WHERE tenant_id=:t"),
                {"t": tid},
            )
        assert APPEND_ONLY_MSG in str(exc_info.value)


# ===========================================================================
# G2 — RLS isolation: tenant B can't see A; NULL-tenant row hidden.
# ===========================================================================
@pytest.mark.asyncio
async def test_g2_rls_isolation_and_null_tenant_hidden(app_engine, pg_engine):
    """G2: the RLS predicate ``tenant_id = app.tenant_id`` scopes reads — a
    tenant sees only its own rows, never another tenant's, never a NULL-tenant
    system row (NULL never equals the GUC)."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()
    await _seed_tenant(app_engine, t_a)
    await _seed_tenant(app_engine, t_b)

    # Tenant A writes its own row.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(t_a)}
        )
        await c.execute(
            text(
                "INSERT INTO audit_log (action, outcome) "
                "VALUES ('workflow.delete', 'success')"
            )
        )
        await c.commit()

    # A NULL-tenant system row — only the superuser can insert it (the INSERT
    # policy's WITH CHECK forbids NULL for vibecanvas_app).
    async with pg_engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO audit_log (tenant_id, action, outcome) "
                "VALUES (NULL, 'auth.login_failure', 'failure')"
            )
        )

    # Tenant B → sees nothing of A's, nor the NULL row.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(t_b)}
        )
        rows = (await c.execute(text("SELECT action, tenant_id FROM audit_log"))).all()
    assert rows == [], f"RLS leak — tenant B saw {rows}"

    # Tenant A → sees ONLY its own row.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(t_a)}
        )
        rows = (await c.execute(text("SELECT action, tenant_id FROM audit_log"))).all()
    assert [r[0] for r in rows] == ["workflow.delete"]
    assert rows[0][1] == t_a


# ===========================================================================
# G3 — atomic resource write: rollback → zero rows.
# ===========================================================================
@pytest.mark.asyncio
async def test_g3_resource_audit_rolls_back_atomically(app_engine):
    """G3: ``record_audit`` ORM-adds into the action's tenant session, so a
    rollback of the surrounding transaction takes the audit row with it."""
    tid = uuid.uuid4()
    await _seed_tenant(app_engine, tid)

    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tid)}
        )
        await record_audit(
            s,
            action=actions.WORKFLOW_DELETE,
            actor_user_id=None,
            actor_email="u@e.com",
            target_type=actions.TARGET_WORKFLOW,
            target_id="wf_1",
            target_name="WF",
            outcome="success",
        )
        await s.flush()
        # Row is visible inside the open transaction.
        assert (await s.execute(text("SELECT count(*) FROM audit_log"))).scalar() == 1
        await s.rollback()
        # Re-bind the GUC (rollback reset it) and assert the row is gone.
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tid)}
        )
        n = (await s.execute(text("SELECT count(*) FROM audit_log"))).scalar()
    assert n == 0


# ===========================================================================
# G4 — auth-failure trail: unknown-email → tenant_id IS NULL.
# ===========================================================================
@pytest.mark.asyncio
async def test_g4_unknown_email_failure_has_null_tenant(admin_engine):
    """G4: an unknown-email login failure is recorded via the admin engine with
    an explicit NULL tenant_id (it never gets GUC-filled), preserving the
    forensic trail of who attempted to log in."""
    pg_engine = admin_engine
    email = f"ghost-{uuid.uuid4().hex[:8]}@nope.com"
    await record_auth_audit(
        action=actions.AUTH_LOGIN_FAILURE,
        actor_user_id=None,
        actor_email=email,
        tenant_id=None,
        outcome="failure",
        audit_ctx=None,
        meta={"reason": "unknown_email"},
    )
    from vibecanvas_api.security.audit_protection import audit_lookup_digest

    digest = audit_lookup_digest("actor_email", email)
    async with pg_engine.connect() as c:
        row = (
            await c.execute(
                text(
                    "SELECT tenant_id, actor_user_id, actor_email, actor_lookup_hash, "
                    "action, outcome FROM audit_log WHERE actor_lookup_hash = :digest"
                ),
                {"digest": digest},
            )
        ).first()
    assert row is not None
    assert row.tenant_id is None  # explicit NULL stuck (not GUC-filled)
    assert row.actor_user_id is None
    assert row.actor_email is None
    assert row.actor_lookup_hash == digest
    assert row.action == "auth.login_failure"
    assert row.outcome == "failure"


# ===========================================================================
# G5 — no-secrets: scan every column + meta for the literal secret values.
# ===========================================================================
@pytest.mark.asyncio
async def test_g5_no_secret_leaks_anywhere(pg_engine, app_engine, admin_engine):
    """G5: across the auth path (a plaintext password), the deployment
    rotate-key path (a new ``vc_`` plaintext key), and the mcp credential
    path (a bearer ``auth_config`` token), NONE of the secret literals may
    appear in ANY audit column or in ``meta::text``."""
    from vibecanvas_api.routes.deployments import rotate_key
    from vibecanvas_api.routes.mcp_servers import PatchBody, patch_mcp_server
    from vibecanvas_api.storage.db import session_scope

    # --- (a) auth path: a plaintext password.
    password = f"PlainPw-{uuid.uuid4().hex}"
    auth_email = f"sec-{uuid.uuid4().hex[:8]}@example.com"
    await record_auth_audit(
        action=actions.AUTH_LOGIN_FAILURE,
        actor_user_id=None,
        actor_email=auth_email,
        tenant_id=None,
        outcome="failure",
        audit_ctx=None,
        meta={"reason": "bad_password"},  # never the pw itself
    )
    from vibecanvas_api.security.audit_protection import audit_lookup_digest

    async with pg_engine.connect() as c:
        auth_blob = "\n".join(
            "|".join("" if v is None else str(v) for v in r)
            for r in await c.execute(
                text(
                    "SELECT tenant_id::text, actor_user_id::text, actor_email, "
                    "       action, target_type, target_id, target_name, outcome, "
                    "       ip_address, user_agent, request_id, meta::text, "
                    "       private_ciphertext "
                    "FROM audit_log WHERE actor_lookup_hash = :digest"
                ),
                {"digest": audit_lookup_digest("actor_email", auth_email)},
            )
        )
    assert password not in auth_blob

    # --- (b) deployment rotate-key: a new plaintext vc_ key.
    t, u = await _seed_tenant_user(pg_engine)
    wf = await _seed_wf(app_engine, t, u)
    dep_id = await _seed_api_dep(app_engine, t, u, wf, name="RotateMe")
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        resp = await rotate_key(
            dep_id=dep_id,
            request=_StubRequest(),
            ctx=ctx,
            session=s,
            service=_AllowAuthz(),
        )
        await s.commit()
    new_key = resp["api_key"]
    assert new_key.startswith("vc_")

    # --- (c) mcp credential_change: a bearer token in auth_config.
    sid = await _seed_mcp_server(pg_engine, t, u, token="OLD-tok-SECRET")
    new_token = "NEW-tok-SECRET-vc"
    with patch(
        "vibecanvas_api.routes.mcp_servers.handshake_one", new_callable=AsyncMock
    ) as mock_hs:
        mock_hs.return_value = {
            "status": "ok",
            "tool_count": 0,
            "tool_names": [],
            "tools": [],
        }
        async with session_scope(tenant_id=str(t)) as s:
            body = PatchBody.model_validate(
                {"auth_config": {"type": "bearer", "token": new_token}}
            )
            await patch_mcp_server(
                server_id=sid,
                body=body,
                request=_StubRequest(),
                ctx=ctx,
                session=s,
                service=_AllowAuthz(),
            )
            await s.commit()

    # One blob covering EVERY audit column + meta for this tenant.
    blob = await _audit_blob(pg_engine, t)
    assert new_key not in blob, "rotated vc_ key leaked into an audit row"
    assert new_token not in blob, "new bearer token leaked into an audit row"
    assert "OLD-tok-SECRET" not in blob, "old bearer token leaked into an audit row"


# ===========================================================================
# G6 — read API: tenant-scoped + action/outcome/from/to filters + cursor.
# ===========================================================================
class _StubCtx:
    def __init__(self, tenant_id, user_id, email="actor@example.com"):
        self.tenant_id = str(tenant_id)
        self.user_id = str(user_id)
        self.active_organization_id = str(tenant_id)
        self.session_id = "test-session"
        self.session_generation = 1
        self.membership_id = "test-membership"
        self.membership_role = "owner"
        self.membership_status = "active"
        self.authentication_strength = "password"
        self.email = email


class _StubRequest:
    def __init__(self, ip="1.2.3.4", ua="pytest/1"):
        self.headers = {"X-Forwarded-For": ip, "User-Agent": ua}
        self.client = type("C", (), {"host": ip})()
        self.state = SimpleNamespace(request_id="audit-test")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")


def _call(session, ctx, **kw):
    from vibecanvas_api.routes.audit import list_audit

    params = dict(
        action=None, outcome=None, ts_from=None, ts_to=None, cursor=None, limit=50
    )
    params.update(kw)
    return list_audit(
        request=_StubRequest(),
        ctx=ctx,
        session=session,
        service=_AllowAuthz(),
        **params,
    )


@pytest.mark.asyncio
async def test_g6_read_api_scope_filters_and_cursor(pg_engine):
    """G6: GET /audit is tenant-scoped via RLS, honours action/outcome/from/to
    filters, and paginates with a stable keyset cursor (no dupes / no gaps)."""
    from vibecanvas_api.storage.db import session_scope

    ta, ua = await _seed_tenant_user(pg_engine)
    tb, _ = await _seed_tenant_user(pg_engine)
    base = datetime(2026, 5, 30, 12, 0, tzinfo=timezone.utc)

    # tenant A: 5 rows, mixed action/outcome, increasing times.
    a_ids = []
    specs = [
        ("auth.logout", "success"),
        ("workflow.delete", "success"),
        ("workflow.delete", "failure"),
        ("deployment.create", "success"),
        ("kb.delete", "success"),
    ]
    for i, (act, out) in enumerate(specs):
        aid = await _seed_audit_row(
            pg_engine,
            ta,
            action=act,
            outcome=out,
            created_at=base + timedelta(minutes=i),
        )
        a_ids.append(str(aid))
    # tenant B: an isolated row.
    await _seed_audit_row(
        pg_engine, tb, action="kb.delete", created_at=base + timedelta(hours=1)
    )

    async with session_scope(tenant_id=str(ta)) as s:
        # --- tenant scope + newest-first.
        out = await _call(s, _StubCtx(ta, ua))
        seen = [it["action"] for it in out["items"]]
        assert seen == [
            "kb.delete",
            "deployment.create",
            "workflow.delete",
            "workflow.delete",
            "auth.logout",
        ]

        # --- action filter.
        wf = await _call(s, _StubCtx(ta, ua), action="workflow.delete")
        assert {it["action"] for it in wf["items"]} == {"workflow.delete"}
        assert len(wf["items"]) == 2

        # --- action + outcome filter.
        wf_ok = await _call(
            s, _StubCtx(ta, ua), action="workflow.delete", outcome="success"
        )
        assert len(wf_ok["items"]) == 1
        assert wf_ok["items"][0]["outcome"] == "success"

        # --- from/to time-window filter (rows at +1m..+3m inclusive of bounds).
        windowed = await _call(
            s,
            _StubCtx(ta, ua),
            ts_from=base + timedelta(minutes=1),
            ts_to=base + timedelta(minutes=3),
        )
        assert len(windowed["items"]) == 3
        assert all(
            base + timedelta(minutes=1)
            <= datetime.fromisoformat(it["created_at"])
            <= base + timedelta(minutes=3)
            for it in windowed["items"]
        )

        # --- cursor stability: limit=2, follow next_cursor → all 5, no dupes.
        collected, cursor, pages = [], None, 0
        while True:
            page = await _call(s, _StubCtx(ta, ua), cursor=cursor, limit=2)
            pages += 1
            collected.extend(it["audit_id"] for it in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
            assert pages < 10
    assert len(collected) == 5
    assert len(set(collected)) == 5  # no dupes
    assert set(collected) == set(a_ids)  # no gaps, tenant-scoped
    assert collected[0] == a_ids[-1]  # newest first across pages
    assert collected[-1] == a_ids[0]


# ===========================================================================
# G7 — every taxonomy action produces a readable row.
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("action", sorted(actions.AUDIT_ACTIONS))
async def test_g7_every_taxonomy_action_produces_a_row(action, pg_engine):
    """G7: parametrized over the complete taxonomy. Each action is written
    through the SAME service paths the production hooks use and read back —
    proving every action in the CHECK constraint is wireable end-to-end:

    * ``auth.*`` actions go through ``record_auth_audit`` (admin engine, the
      auth-hook mechanic). Failures carry NULL tenant; the rest carry a tenant.
    * resource/credential actions go through ``record_audit`` on a tenant
      session (the resource-hook mechanic), committing on that session.
    """
    assert action in actions.AUDIT_ACTIONS

    if action.startswith(("auth.", "purge.")):
        # Auth path — record_auth_audit via the injected admin engine.
        from vibecanvas_api.storage import db as db_mod

        token = db_mod.__dict__.get("_admin_engine")
        db_mod._admin_engine = pg_engine
        try:
            email = f"g7-{uuid.uuid4().hex[:8]}@example.com"
            is_failure = action == actions.AUTH_LOGIN_FAILURE
            tid = None if is_failure else uuid.uuid4()
            if tid is not None:
                await _seed_tenant(pg_engine, tid)
            await record_auth_audit(
                action=action,
                actor_user_id=None,
                actor_email=email,
                tenant_id=tid,
                outcome="failure" if is_failure else "success",
                audit_ctx=None,
                meta={"g7": action},
            )
            from vibecanvas_api.security.audit_protection import (
                audit_lookup_digest,
            )

            async with pg_engine.connect() as c:
                row = (
                    await c.execute(
                        text(
                            "SELECT action, outcome, tenant_id FROM audit_log "
                            "WHERE actor_lookup_hash = :digest"
                        ),
                        {"digest": audit_lookup_digest("actor_email", email)},
                    )
                ).first()
        finally:
            db_mod._admin_engine = token
        assert row is not None
        assert row.action == action
        if is_failure:
            assert row.tenant_id is None
        else:
            assert row.tenant_id == tid
    else:
        # Resource/credential path — record_audit on a tenant session.
        from vibecanvas_api.storage.db import session_scope

        t, u = await _seed_tenant_user(pg_engine)
        target_type = {
            actions.DEPLOYMENT_CREATE: actions.TARGET_DEPLOYMENT,
            actions.DEPLOYMENT_DELETE: actions.TARGET_DEPLOYMENT,
            actions.DEPLOYMENT_KEY_ROTATE: actions.TARGET_DEPLOYMENT,
            actions.MCP_SERVER_CREATE: actions.TARGET_MCP_SERVER,
            actions.MCP_SERVER_DELETE: actions.TARGET_MCP_SERVER,
            actions.MCP_SERVER_CREDENTIAL_CHANGE: actions.TARGET_MCP_SERVER,
            actions.WORKFLOW_DELETE: actions.TARGET_WORKFLOW,
            actions.KB_DELETE: actions.TARGET_KB,
        }.get(
            action,
            {
                "organization": actions.TARGET_ORGANIZATION,
                "share": actions.TARGET_SHARE,
                "service_account": actions.TARGET_SERVICE_ACCOUNT,
                "secret": actions.TARGET_SECRET,
            }.get(action.split(".", 1)[0], action.split(".", 1)[0]),
        )
        async with session_scope(tenant_id=str(t)) as s:
            await record_audit(
                s,
                action=action,
                actor_user_id=u,
                actor_email="g7@example.com",
                target_type=target_type,
                target_id="obj_1",
                target_name="Obj",
                outcome="success",
                audit_ctx=None,
                meta={"g7": action},
            )
            await s.commit()
        rows = await _audit_rows(pg_engine, t)
        assert [r["action"] for r in rows] == [action]
        assert rows[0]["target_type"] == target_type
        assert rows[0]["outcome"] == "success"


def test_g7_covers_full_taxonomy():
    """Guard: the expanded security-control taxonomy stays comprehensive."""
    assert len(actions.AUDIT_ACTIONS) >= 30


# ---------------------------------------------------------------------------
# Resource seeds (mirror ``test_audit_resource_hooks.py``) used by G5.
# ---------------------------------------------------------------------------
async def _seed_wf(app_engine, tenant_id, user_id):
    from vibecanvas_api.storage.workflow_repo import WorkflowRepo

    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    async with session_scope(tenant_id=str(tenant_id)) as session:
        await WorkflowRepo(session, str(user_id)).create_workflow(
            wf_id=wf_id,
            name="Audit Workflow",
        )
    return wf_id


async def _seed_api_dep(app_engine, tenant_id, user_id, wf_id, *, name="DepName"):
    import hashlib

    dep_id = uuid.uuid4()
    h = hashlib.sha256(f"key-{uuid.uuid4().hex}".encode()).hexdigest()
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"), {"t": str(tenant_id)}
        )
        await c.execute(
            text(
                "INSERT INTO deployments("
                "id, tenant_id, user_id, owner_id, wf_id, name, slug, "
                "trigger_type, version_pin, pinned_major, pinned_sub, api_key_hash"
                ") VALUES ("
                ":id, :t, :u, :u, :w, :n, :s, 'api', 'specific', 1, 0, :h)"
            ),
            {
                "id": dep_id,
                "t": tenant_id,
                "u": user_id,
                "w": wf_id,
                "n": name,
                "s": f"dep-{uuid.uuid4().hex[:6]}",
                "h": h,
            },
        )
        await c.commit()
    return dep_id


async def _seed_mcp_server(
    pg_engine,
    tenant_id,
    user_id,
    *,
    name="McpName",
    tool_prefix="mcpx",
    token="tok-SEEDED-SECRET",
):
    sid = uuid.uuid4()
    async with session_scope(tenant_id=str(tenant_id)) as session:
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=tenant_id,
            purpose="mcp_bearer_token",
            resource_type="mcp_installation",
            resource_id=sid,
            plaintext=token,
        )
        await McpServersRepo(session).insert(
            id=sid,
            tenant_id=tenant_id,
            user_id=user_id,
            name=name,
            tool_prefix=tool_prefix,
            transport="sse",
            endpoint="https://events.example.test/sse",
            auth_config={"type": "bearer"},
            auth_secret_ref=secret_ref,
            enabled=True,
            last_handshake_status="ok",
            last_tool_count=0,
            last_tool_names=[],
        )
    return sid
