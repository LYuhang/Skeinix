"""T1 — AuditLog model shape + the action taxonomy + migration 009
(append-only trigger + FORCE RLS isolation).

The DB-level tests assert as the non-superuser ``vibecanvas_app`` role via the
``app_engine`` fixture (owner of the table; FORCE RLS binds even the owner). A
superuser (``pg_engine``) bypasses RLS + ownership, so the append-only / RLS
gates would be meaningless under it — those checks must run as ``vibecanvas_app``.
Seeding a NULL-tenant row (which the INSERT policy forbids) uses ``pg_engine``.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from vibecanvas_api.audit import actions
from vibecanvas_api.storage.models import AuditLog


# ---------------------------------------------------------------------------
# Step 2 — model shape + taxonomy (pure, no DB)
# ---------------------------------------------------------------------------
def test_taxonomy_covers_security_control_plane_actions():
    assert len(actions.AUDIT_ACTIONS) >= 30
    assert "auth.login_failure" in actions.AUDIT_ACTIONS
    assert "deployment.key_rotate" in actions.AUDIT_ACTIONS
    # dropped actions must NOT be present (no endpoint)
    assert "auth.password_change" not in actions.AUDIT_ACTIONS
    assert "auth.passkey_register" in actions.AUDIT_ACTIONS
    assert "auth.passkey_verify" in actions.AUDIT_ACTIONS
    assert "auth.passkey_remove" in actions.AUDIT_ACTIONS
    assert "auth.mfa_enroll" not in actions.AUDIT_ACTIONS
    assert "auth.mfa_challenge" not in actions.AUDIT_ACTIONS
    assert "auth.session_revoke" in actions.AUDIT_ACTIONS
    assert "share.grant" in actions.AUDIT_ACTIONS
    assert "service_account.status_change" in actions.AUDIT_ACTIONS
    assert "purge.completed" in actions.AUDIT_ACTIONS


def test_auditlog_columns_and_nullability():
    cols = AuditLog.__table__.columns
    assert cols["tenant_id"].nullable is True          # D5
    assert cols["actor_user_id"].nullable is True
    assert cols["outcome"].nullable is False
    assert cols["meta"].name == "meta"                 # not metadata_ (review)
    assert "updated_at" not in cols                    # append-only


def test_auditlog_fk_on_delete_set_null():
    fks = {fk.column.table.name: fk for fk in AuditLog.__table__.foreign_keys}
    assert fks["tenants"].ondelete == "SET NULL"       # audit survives tenant delete
    assert fks["users"].ondelete == "SET NULL"


# ---------------------------------------------------------------------------
# Step 6 — append-only trigger (G1): as vibecanvas_app, UPDATE/DELETE raise.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_append_only_update_and_delete_raise(app_engine):
    """G1: as vibecanvas_app, UPDATE and DELETE on audit_log must raise.

    The append-only BEFORE UPDATE OR DELETE trigger is ownership-independent
    (REVOKE is ineffective vs the owner), so it RAISEs regardless of role.
    """
    tid = uuid.uuid4()
    # Seed a tenant + a single audit row (tenant_id auto-fills from the GUC).
    async with app_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
            {"t": tid},
        )

    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                        {"t": str(tid)})
        await c.execute(text(
            "INSERT INTO audit_log (action, outcome) "
            "VALUES ('auth.logout', 'success')"
        ))
        await c.commit()

    # UPDATE → trigger RAISEs.
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                        {"t": str(tid)})
        with pytest.raises(Exception):
            await c.execute(text("UPDATE audit_log SET outcome='failure'"))
            await c.commit()

    # DELETE → trigger RAISEs.
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                        {"t": str(tid)})
        with pytest.raises(Exception):
            await c.execute(text("DELETE FROM audit_log"))
            await c.commit()


# ---------------------------------------------------------------------------
# Step 9 — RLS isolation (G2): tenant B can't see A; NULL-tenant row hidden.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rls_tenant_isolation_and_null_hidden(app_engine, pg_engine):
    """G2: tenant B can't see tenant A's row; a NULL-tenant row is hidden
    from every tenant (RLS predicate ``tenant_id = app.tenant_id`` excludes
    NULL)."""
    t_a, t_b = uuid.uuid4(), uuid.uuid4()

    async with app_engine.begin() as c:
        for t in (t_a, t_b):
            await c.execute(
                text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'x')"),
                {"t": t},
            )

    # Tenant A writes its own row (tenant_id auto-fills from GUC).
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                        {"t": str(t_a)})
        await c.execute(text(
            "INSERT INTO audit_log (action, outcome) "
            "VALUES ('workflow.delete', 'success')"
        ))
        await c.commit()

    # A system NULL-tenant row — only the superuser engine can insert it
    # (the INSERT policy's WITH CHECK forbids NULL for vibecanvas_app).
    async with pg_engine.begin() as c:
        await c.execute(text(
            "INSERT INTO audit_log (tenant_id, action, outcome) "
            "VALUES (NULL, 'auth.login_failure', 'failure')"
        ))

    # Tenant B → sees nothing (not A's row, not the NULL row).
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                        {"t": str(t_b)})
        rows = (await c.execute(text(
            "SELECT action, tenant_id FROM audit_log"))).all()
    assert rows == [], f"RLS leak — tenant B saw {rows}"

    # Tenant A → sees ONLY its own row, never the NULL-tenant row.
    async with app_engine.connect() as c:
        await c.execute(text("SELECT set_config('app.tenant_id', :t, false)"),
                        {"t": str(t_a)})
        rows = (await c.execute(text(
            "SELECT action, tenant_id FROM audit_log"))).all()
    assert [r[0] for r in rows] == ["workflow.delete"]
    assert rows[0][1] is not None
