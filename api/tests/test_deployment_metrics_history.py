"""Deployments T13 — metrics + history endpoint shape.

Deployment invocation observability no longer uses Task rows. These tests seed
a deployment and verify the endpoint shape, visibility checks, and empty-state
contract until a deployment-owned run/log store is implemented. Tests target:

* metrics — bucketed shape, 400 on unknown bucket, 404 cross-tenant.
* history — empty page shape.
* route mount — both paths are advertised by the FastAPI app.

Strategy mirrors ``test_deployment_test_invoke.py``: superuser engine
for auth tables, ``set_config('app.tenant_id', ...)`` for RLS-bound
inserts, and direct handler calls via ``session_scope(tenant_id=...)``.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision


async def _seed_dep(pg_engine, app_engine):
    """Returns ``(tenant_id, user_id, dep_id, base_dt)``.

    ``base_dt`` is only used to bracket metrics query windows.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
    dep_id = uuid.uuid4()
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
            {
                "u": user_id, "t": tenant_id,
                "e": f"t13-{uuid.uuid4().hex[:6]}@example.com",
            },
        )
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.workflow_repo import WorkflowRepo

    async with session_scope(tenant_id=str(tenant_id)) as session:
        await WorkflowRepo(session, str(user_id)).create_workflow(
            wf_id=wf_id,
            name="Metrics Workflow",
        )
        await session.execute(
            text(
                "INSERT INTO deployments("
                "id, tenant_id, user_id, owner_id, wf_id, name, slug, "
                "trigger_type, version_pin, pinned_major, pinned_sub, "
                "api_key_hash) "
                "VALUES (:id, :t, :u, :u, :w, 'M', :s, "
                "'api', 'head', NULL, NULL, 'h')"
            ),
            {
                "id": dep_id, "t": tenant_id, "u": user_id, "w": wf_id,
                "s": f"m-{uuid.uuid4().hex[:6]}",
            },
        )
        base = datetime.now(timezone.utc) - timedelta(hours=1)
    return tenant_id, user_id, dep_id, base


class _Ctx:
    """Complete AuthContext stand-in for handler-direct authorization."""

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
        self.email = "deployment-metrics-test@example.invalid"


class _StubRequest:
    def __init__(self):
        self.headers = {}
        self.client = None
        self.state = SimpleNamespace(request_id="test-request")
        self.app = SimpleNamespace(state=SimpleNamespace(openfga_client=None))


class _AllowAuthz:
    async def check(self, *args, **kwargs):
        return Decision(allowed=True, reason_code="test_fixture")


@pytest.mark.asyncio
async def test_metrics_returns_bucketed_series(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import metrics
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, user_id, dep_id, base = await _seed_dep(pg_engine, app_engine)
    ctx = _Ctx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        resp = await metrics(
            dep_id=dep_id,
            request=_StubRequest(),
            from_=base - timedelta(hours=1),
            to=base + timedelta(hours=20),
            bucket="hour",
            ctx=ctx,
            session=s,
            service=_AllowAuthz(),
        )
    assert "series" in resp
    assert resp["bucket"] == "hour"
    assert resp["series"] == []


@pytest.mark.asyncio
async def test_metrics_invalid_bucket_400(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import metrics
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, user_id, dep_id, base = await _seed_dep(pg_engine, app_engine)
    ctx = _Ctx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        with pytest.raises(HTTPException) as exc:
            await metrics(
                dep_id=dep_id,
                request=_StubRequest(),
                from_=base,
                to=base + timedelta(hours=1),
                bucket="minute",
                ctx=ctx,
                session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_metrics_other_tenant_404(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import metrics
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_a, _, dep_a, base = await _seed_dep(pg_engine, app_engine)
    tenant_b = uuid.uuid4()
    user_b = uuid.uuid4()
    async with pg_engine.begin() as c:
        await c.execute(
            text("INSERT INTO tenants(tenant_id, name) VALUES (:t, 'b')"),
            {"t": tenant_b},
        )
        await c.execute(
            text(
                "INSERT INTO users(user_id, tenant_id, email) "
                "VALUES (:u, :t, :e)"
            ),
            {
                "u": user_b, "t": tenant_b,
                "e": f"tb-{uuid.uuid4().hex[:6]}@example.com",
            },
        )

    ctx = _Ctx(tenant_b, user_b)
    async with session_scope(tenant_id=str(tenant_b)) as s:
        with pytest.raises(HTTPException) as exc:
            await metrics(
                dep_id=dep_a,
                request=_StubRequest(),
                from_=base,
                to=base + timedelta(hours=1),
                bucket="hour",
                ctx=ctx,
                session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_history_returns_empty_page(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import history
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, user_id, dep_id, _ = await _seed_dep(pg_engine, app_engine)
    ctx = _Ctx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        page1 = await history(
            dep_id=dep_id, request=_StubRequest(), limit=10, cursor=None,
            status_filter=None, ctx=ctx, session=s, service=_AllowAuthz(),
        )
    assert page1 == {"items": [], "next_cursor": None, "limit": 10}


@pytest.mark.asyncio
async def test_history_status_filter_keeps_empty_shape(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import history
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, user_id, dep_id, _ = await _seed_dep(pg_engine, app_engine)
    ctx = _Ctx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        resp = await history(
            dep_id=dep_id, request=_StubRequest(), limit=50, cursor=None,
            status_filter=["failed"], ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
    assert resp == {"items": [], "next_cursor": None, "limit": 50}


@pytest.mark.asyncio
async def test_history_ignores_cursor_until_run_store_exists(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import history
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, user_id, dep_id, _ = await _seed_dep(pg_engine, app_engine)
    ctx = _Ctx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        resp = await history(
            dep_id=dep_id, request=_StubRequest(), limit=10,
            cursor="!!!not-base64!!!", status_filter=None, ctx=ctx,
            session=s, service=_AllowAuthz(),
        )
    assert resp == {"items": [], "next_cursor": None, "limit": 10}


@pytest.mark.asyncio
async def test_metrics_and_history_read_invocation_store(
    pg_engine, app_engine, monkeypatch, pg_url,
):
    from vibecanvas_api.routes.deployments import history, metrics
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope
    monkeypatch.setattr(db_mod, "_admin_engine", None)
    monkeypatch.setenv("ADMIN_DATABASE_URL", pg_url)

    tenant_id, user_id, dep_id, base = await _seed_dep(pg_engine, app_engine)
    inv_id = uuid.uuid4()
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(tenant_id)},
        )
        wf_id = (
            await c.execute(
                text("SELECT wf_id FROM deployments WHERE id = :id"),
                {"id": dep_id},
            )
        ).scalar_one()
        await c.execute(
            text(
                """
                INSERT INTO deployment_invocations(
                    id, tenant_id, deployment_id, wf_id, trigger_type, source,
                    status, submitted_at, started_at, finished_at, latency_ms
                )
                VALUES (
                    :id, :t, :d, :w, 'api', 'sync_api', 'succeeded',
                    :ts, :ts, :done, 123.4
                )
                """
            ),
            {
                "id": inv_id,
                "t": tenant_id,
                "d": dep_id,
                "w": wf_id,
                "ts": base,
                "done": base + timedelta(seconds=2),
            },
        )
        await c.commit()

    ctx = _Ctx(tenant_id, user_id)
    async with session_scope(tenant_id=str(tenant_id)) as s:
        metric_resp = await metrics(
            dep_id=dep_id,
            request=_StubRequest(),
            from_=base - timedelta(minutes=1),
            to=base + timedelta(minutes=10),
            bucket="hour",
            ctx=ctx,
            session=s,
            service=_AllowAuthz(),
        )
        history_resp = await history(
            dep_id=dep_id,
            request=_StubRequest(),
            limit=10,
            cursor=None,
            status_filter=None,
            ctx=ctx,
            session=s,
            service=_AllowAuthz(),
        )

    assert metric_resp["series"]
    assert metric_resp["series"][0]["calls"] == 1
    assert metric_resp["series"][0]["errors"] == 0
    assert history_resp["items"][0]["id"] == str(inv_id)
    assert history_resp["items"][0]["source"] == "sync_api"
    assert history_resp["items"][0]["latency_ms"] == 123.4


def test_routes_mounted():
    from vibecanvas_api.app import build_app
    from vibecanvas_api.authorization.manifest import application_route_contexts
    app = build_app()
    paths = {r.path for r in application_route_contexts(app)}
    assert any("/deployments/{dep_id}/metrics" in p for p in paths)
    assert any("/deployments/{dep_id}/history" in p for p in paths)
