"""T3 — record_audit (resource, in-session) + record_auth_audit (admin, NULL ok).

Two paths, two test mechanics (mirroring the real conftest fixtures):

* Resource path (``record_audit``): an inline ``vibecanvas_app``-bound session
  (``app_engine``) with ``app.tenant_id`` set from the GUC — FORCE RLS applies,
  the migration-009 server-default fills tenant_id. Mirrors
  ``test_audit_repo.py`` / the T1 schema test.
* Auth path (``record_auth_audit``): the superuser ``pg_engine`` injected as
  ``db._admin_engine`` so ``session_scope_admin`` runs RLS-bypassing — the
  explicit NULL-tenant raw INSERT is then permitted. Mirrors
  ``test_batch_submit_and_reconciler.py:58``.
"""
from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from vibecanvas_api.audit import actions
from vibecanvas_api.audit.service import record_audit, record_auth_audit


# ---------------------------------------------------------------------------
# Fixtures — wire the plan's placeholder names to the real conftest fixtures.
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def app_tenant_session(app_engine):
    """A ``vibecanvas_app``-bound AsyncSession with ``app.tenant_id`` set so
    FORCE RLS applies and the migration-009 server-default fills tenant_id.
    Seeds a tenant row (FK target) first via the same engine."""
    tid = uuid.uuid4()
    async with app_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tid},
        )
    maker = async_sessionmaker(app_engine, expire_on_commit=False)
    async with maker() as s:
        await s.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tid)},
        )
        s.info["tenant_id"] = tid
        yield s
        await s.rollback()


@pytest_asyncio.fixture
async def admin_engine_injected(monkeypatch, pg_engine):
    """Inject the superuser ``pg_engine`` as ``db._admin_engine`` so
    ``session_scope_admin`` (used by ``record_auth_audit``) runs RLS-bypassing
    — otherwise the explicit NULL-tenant INSERT fails FORCE RLS as
    ``vibecanvas_app``. Mirrors ``test_batch_submit_and_reconciler.py:58``."""
    from vibecanvas_api.storage import db as db_mod
    monkeypatch.setattr(db_mod, "_admin_engine", pg_engine)
    yield pg_engine


# ---------------------------------------------------------------------------
# Resource path — record_audit (ORM add into the tenant session).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_audit_resource_path_atomic(app_tenant_session):
    """record_audit adds a row to the tenant session; tenant_id auto-fills."""
    s = app_tenant_session  # vibecanvas_app session w/ app.tenant_id set
    await record_audit(
        s, action=actions.WORKFLOW_DELETE, actor_user_id=None,
        actor_email="u@e.com", target_type=actions.TARGET_WORKFLOW,
        target_id="wf_1", target_name="My WF", outcome="success",
        audit_ctx=None, meta={"k": "v"},
    )
    await s.flush()
    from vibecanvas_api.audit.repo import AuditRepo

    row = (await AuditRepo(s).list_for_tenant())[0]
    assert row.action == "workflow.delete"
    assert row.tenant_id is not None          # server-default filled from GUC
    assert row.tenant_id == s.info["tenant_id"]
    assert row.meta == {"k": "v"}


@pytest.mark.asyncio
async def test_resource_audit_rolls_back_with_action(app_tenant_session):
    """G3: if the surrounding transaction rolls back, the audit row is gone."""
    s = app_tenant_session
    await record_audit(
        s, action=actions.KB_DELETE, actor_user_id=None,
        actor_email="u@e.com", target_type=actions.TARGET_KB,
        target_id="kb_1", target_name="KB", outcome="success",
    )
    await s.flush()
    await s.rollback()
    # The rollback reset the GUC; re-bind so the SELECT (RLS) can run, then
    # assert the row is gone (rolled back atomically with the action).
    await s.execute(
        text("SELECT set_config('app.tenant_id', :t, false)"),
        {"t": str(s.info["tenant_id"])},
    )
    n = (await s.execute(text("SELECT count(*) FROM audit_log"))).scalar()
    assert n == 0


# ---------------------------------------------------------------------------
# Auth path — record_auth_audit (admin raw INSERT, explicit NULL tenant).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_auth_audit_null_tenant_unknown_email(admin_engine_injected):
    """G4: unknown-email login failure → tenant_id NULL, via admin raw INSERT."""
    pg_engine = admin_engine_injected
    # Unique marker email so the read-back is robust even though audit_log is
    # not in conftest's truncate list (rows from sibling tests may coexist).
    email = f"ghost-{uuid.uuid4().hex[:8]}@nope.com"
    await record_auth_audit(
        action=actions.AUTH_LOGIN_FAILURE, actor_user_id=None,
        actor_email=email, tenant_id=None, outcome="failure",
        audit_ctx=None, meta={"reason": "unknown_email"},
    )
    from vibecanvas_api.security.audit_protection import audit_lookup_digest

    # Unknown identities retain only an irreversible correlation token.
    async with pg_engine.connect() as c:
        row = (await c.execute(text(
            "SELECT tenant_id, actor_user_id, actor_email, actor_lookup_hash, "
            "action, outcome, meta FROM audit_log "
            "WHERE actor_lookup_hash = :digest"
        ), {"digest": audit_lookup_digest("actor_email", email)})).first()
    assert row is not None
    assert row.tenant_id is None             # explicit NULL stuck (not GUC-filled)
    assert row.actor_user_id is None
    assert row.actor_email is None
    assert row.actor_lookup_hash == audit_lookup_digest("actor_email", email)
    assert row.action == "auth.login_failure"
    assert row.outcome == "failure"
    assert row.meta == {}
