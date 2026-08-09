"""Deployments T5 — list / get / patch / delete / rotate-key + workflow-delete guard.

Mirrors T4's handler-direct-call strategy (no httpx ASGI ride, no auth-DI
stack). The plan's TestClient-based path is fragile against ``current_user``
Bearer-token validation; calling the handler directly exercises the same
body validation + repo + secret rotation + soft-delete + scrubbing logic
without standing up the auth DI plumbing.

Coverage matrix (each row → one test):

* list: ``trigger_type`` filter narrows results.
* patch: ``enabled`` toggle round-trips through GET (no stale read).
* patch: invalid cron / invalid IANA tz → 422.
* delete: soft delete hides the row from subsequent GET (404).
* rotate-key: returns ``vc_`` plaintext + invalidates the old key against
  ``resolve_deployment_and_bind_tenant`` (the deployment-side authenticator
  used by every external invoke endpoint).
* rotate-key: 400 for ``trigger_type != 'api'`` (webhook secrets aren't
  rotated through this path).
* G/PATCH/DELETE/rotate-key: 404 for unknown ids.
* workflow-delete guard: enabled deployment → 409 (Spec §10.4).

Secret-scrubbing assertion is enforced inline on every successful read
path — both ``api_key_hash`` and ``hmac_secret`` MUST be absent from
list / get / patch responses.
"""
from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from vibecanvas_api.authorization.types import Decision
from vibecanvas_api.security.secret_service import secret_service
from vibecanvas_api.storage.db import session_scope
from vibecanvas_api.storage.repo_deployments import DeploymentsRepo


# ---------------------------------------------------------------------- seed


async def _seed_minimal_tenant_user_wf(pg_engine, app_engine):
    """Seed one tenant + one user + one workflow.

    Returns ``(tenant_id, user_id, wf_id)``. The tenant + user inserts run
    via the superuser ``pg_engine`` (bypassing RLS); the workflow goes
    through the RLS-aware ``app_engine`` with ``app.tenant_id`` GUC set —
    same shape as ``test_deployments_create.py``.
    """
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    wf_id = f"wf_{uuid.uuid4().hex[:8]}"
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
             "e": f"t5-{uuid.uuid4().hex[:6]}@example.com"},
        )
    from vibecanvas_api.storage.db import session_scope
    from vibecanvas_api.storage.workflow_repo import WorkflowRepo

    async with session_scope(tenant_id=str(tenant_id)) as session:
        await WorkflowRepo(session, str(user_id)).create_workflow(
            wf_id=wf_id,
            name="Deployment Workflow",
        )
    return tenant_id, user_id, wf_id


async def _seed_api_dep(
    app_engine, tenant_id, user_id, wf_id, *, plaintext_key=None,
):
    """Seed an api-type deployment row.

    Returns ``(dep_id, plaintext_key_used)``. The stored ``api_key_hash``
    is SHA-256 of the plaintext — same shape ``generate_api_key`` produces
    and ``DeploymentsRepo.get_by_api_key`` looks up by.
    """
    if plaintext_key is None:
        plaintext_key = f"key-{uuid.uuid4().hex[:12]}"
    h = hashlib.sha256(plaintext_key.encode()).hexdigest()
    dep_id = uuid.uuid4()
    async with session_scope(tenant_id=str(tenant_id)) as session:
        await DeploymentsRepo(session).insert(
            id=dep_id,
            tenant_id=tenant_id,
            user_id=user_id,
            owner_id=user_id,
            wf_id=wf_id,
            name="D",
            slug=f"dep-{uuid.uuid4().hex[:6]}",
            trigger_type="api",
            version_pin="specific",
            pinned_major=1,
            pinned_sub=0,
            api_key_hash=h,
        )
    return dep_id, plaintext_key


async def _seed_webhook_dep(app_engine, tenant_id, user_id, wf_id):
    """Seed a webhook deployment with an opaque SecretService reference."""
    dep_id = uuid.uuid4()
    async with session_scope(tenant_id=str(tenant_id)) as session:
        secret_ref = await secret_service().put_text(
            session,
            tenant_id=tenant_id,
            purpose="deployment_webhook_hmac",
            resource_type="deployment",
            resource_id=dep_id,
            plaintext="whsec_seedsecret",
        )
        await DeploymentsRepo(session).insert(
            id=dep_id,
            tenant_id=tenant_id,
            user_id=user_id,
            owner_id=user_id,
            wf_id=wf_id,
            name="H",
            slug=f"h-{uuid.uuid4().hex[:6]}",
            trigger_type="webhook",
            version_pin="head",
            pinned_major=None,
            pinned_sub=None,
            hmac_secret_ref=secret_ref,
            hmac_secret_version=1,
        )
    return dep_id


class _StubCtx:
    """Lightweight stand-in for ``AuthContext``. The T5 handlers don't
    read fields off the ctx (the tenant scope is enforced via the
    tenant-bound DI session), but they DO type the parameter as
    AuthContext; in tests we pass any duck-typed object."""

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
        self.app = SimpleNamespace(
            state=SimpleNamespace(openfga_client=None),
        )


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


# ---------------------------------------------------------------- list filter


@pytest.mark.asyncio
async def test_list_filters_by_trigger_type(pg_engine, app_engine):
    """GET /deployments?trigger_type=api narrows to api rows; the
    coexisting webhook row is excluded. Secrets are scrubbed from each."""
    from vibecanvas_api.routes.deployments import list_deployments
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    api_a, _ = await _seed_api_dep(app_engine, t, u, w)
    api_b, _ = await _seed_api_dep(app_engine, t, u, w)
    webhook = await _seed_webhook_dep(app_engine, t, u, w)

    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        resp = await list_deployments(
            request=_StubRequest(),
            trigger_type="api", enabled=None, workflow_id=None,
            limit=50, offset=0, ctx=ctx, session=s,
            service=_AllowAuthz((api_a, api_b, webhook)),
        )
    assert len(resp["items"]) == 2
    assert all(d["trigger_type"] == "api" for d in resp["items"])
    # Secret-scrub invariant on every list item.
    for item in resp["items"]:
        assert "api_key_hash" not in item
        assert "hmac_secret" not in item


# -------------------------------------------------------------------- patch


@pytest.mark.asyncio
async def test_patch_toggle_enabled(pg_engine, app_engine):
    """PATCH ``enabled=False`` → response reflects new value (the handler
    re-fetches after UPDATE so the returned dict isn't stale)."""
    from vibecanvas_api.routes.deployments import (
        PatchDeploymentBody, patch_deployment,
    )
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    dep_id, _ = await _seed_api_dep(app_engine, t, u, w)
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        resp1 = await patch_deployment(
            dep_id=dep_id,
            body=PatchDeploymentBody.model_validate({"enabled": False}),
            request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.commit()
    assert resp1["enabled"] is False
    assert "api_key_hash" not in resp1
    assert "hmac_secret" not in resp1

    async with session_scope(tenant_id=str(t)) as s:
        resp2 = await patch_deployment(
            dep_id=dep_id,
            body=PatchDeploymentBody.model_validate({"enabled": True}),
            request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.commit()
    assert resp2["enabled"] is True


@pytest.mark.asyncio
async def test_patch_invalid_cron_422(pg_engine, app_engine):
    """Invalid cron expression in PATCH body → 422 (validated server-side
    because Pydantic's per-field validator doesn't run on a partial PATCH)."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import (
        PatchDeploymentBody, patch_deployment,
    )
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    dep_id, _ = await _seed_api_dep(app_engine, t, u, w)
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await patch_deployment(
                dep_id=dep_id,
                body=PatchDeploymentBody.model_validate(
                    {"cron_expr": "not a cron"}),
                request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_patch_invalid_tz_422(pg_engine, app_engine):
    """Invalid IANA tz in PATCH body → 422."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import (
        PatchDeploymentBody, patch_deployment,
    )
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    dep_id, _ = await _seed_api_dep(app_engine, t, u, w)
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await patch_deployment(
                dep_id=dep_id,
                body=PatchDeploymentBody.model_validate(
                    {"cron_tz": "Mars/Olympus"}),
                request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_patch_404_for_unknown(pg_engine, app_engine):
    """PATCH on a missing id → 404."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import (
        PatchDeploymentBody, patch_deployment,
    )
    from vibecanvas_api.storage.db import session_scope

    t, u, _ = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await patch_deployment(
                dep_id=uuid.uuid4(),
                body=PatchDeploymentBody.model_validate({"enabled": False}),
                request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 404


# --------------------------------------------------------- get + soft-delete


@pytest.mark.asyncio
async def test_get_returns_scrubbed_row(pg_engine, app_engine):
    """GET returns the row WITHOUT ``api_key_hash`` / ``hmac_secret``."""
    from vibecanvas_api.routes.deployments import get_deployment
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    dep_id, _ = await _seed_api_dep(app_engine, t, u, w)
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        dep = await get_deployment(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
    assert dep["id"] == str(dep_id)
    assert dep["trigger_type"] == "api"
    assert "api_key_hash" not in dep
    assert "hmac_secret" not in dep


@pytest.mark.asyncio
async def test_get_404_for_unknown(pg_engine, app_engine):
    """GET on an unknown id → 404."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import get_deployment
    from vibecanvas_api.storage.db import session_scope

    t, u, _ = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await get_deployment(
                dep_id=uuid.uuid4(), request=_StubRequest(), ctx=ctx,
                session=s, service=_AllowAuthz(),
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_soft_delete_hides_from_get(pg_engine, app_engine):
    """DELETE → 204; subsequent GET → 404 (repo filters ``deleted_at IS NULL``)."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import (
        delete_deployment, get_deployment,
    )
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    dep_id, _ = await _seed_api_dep(app_engine, t, u, w)
    ctx = _StubCtx(t, u)

    async with session_scope(tenant_id=str(t)) as s:
        before = await get_deployment(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        assert before["id"] == str(dep_id)
        resp = await delete_deployment(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        # FastAPI Response object — non-None body irrelevant; status is.
        assert resp.status_code == 204
        await s.commit()
    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await get_deployment(
                dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_404_for_unknown(pg_engine, app_engine):
    """DELETE on an unknown id → 404."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import delete_deployment
    from vibecanvas_api.storage.db import session_scope

    t, u, _ = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await delete_deployment(
                dep_id=uuid.uuid4(), request=_StubRequest(), ctx=ctx,
                session=s, service=_AllowAuthz(),
            )
        assert exc.value.status_code == 404


# --------------------------------------------------------------- rotate-key


@pytest.mark.asyncio
async def test_rotate_key_invalidates_old(
    monkeypatch, pg_engine, app_engine,
):
    """POST /rotate-key returns a fresh ``vc_`` plaintext AND invalidates
    the previous plaintext against
    ``resolve_deployment_and_bind_tenant(api_key=...)``.

    The resolver looks up by ``api_key_hash`` under the admin engine. We
    monkeypatch ``db._admin_engine`` to the superuser ``pg_engine`` so
    the resolver bypasses RLS — same pattern as
    ``test_deployments_repo_and_service.py``.
    """
    from vibecanvas_api.routes.deployments import rotate_key
    from vibecanvas_api.services.deployments_service import (
        resolve_deployment_and_bind_tenant,
    )
    from vibecanvas_api.services.tenant_db import tenant_id_var
    from vibecanvas_api.storage import db as db_mod
    from vibecanvas_api.storage.db import session_scope

    monkeypatch.setattr(db_mod, "_admin_engine", pg_engine)

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    old_plaintext = f"oldkey-{uuid.uuid4().hex[:12]}"
    dep_id, _ = await _seed_api_dep(
        app_engine, t, u, w, plaintext_key=old_plaintext,
    )
    ctx = _StubCtx(t, u)

    # Sanity: the old key resolves before rotation.
    tenant_id_var.set(None)
    pre = await resolve_deployment_and_bind_tenant(api_key=old_plaintext)
    assert pre is not None
    assert pre["id"] == dep_id

    # Rotate.
    async with session_scope(tenant_id=str(t)) as s:
        resp = await rotate_key(
            dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
            service=_AllowAuthz(),
        )
        await s.commit()
    new_plaintext = resp["api_key"]
    assert new_plaintext.startswith("vc_")
    assert new_plaintext != old_plaintext

    # Old key MUST stop matching.
    tenant_id_var.set(None)
    assert await resolve_deployment_and_bind_tenant(
        api_key=old_plaintext) is None
    # New key MUST match.
    tenant_id_var.set(None)
    matched = await resolve_deployment_and_bind_tenant(api_key=new_plaintext)
    assert matched is not None
    assert matched["id"] == dep_id


@pytest.mark.asyncio
async def test_rotate_key_400_for_webhook(pg_engine, app_engine):
    """rotate-key on a non-api deployment → 400."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import rotate_key
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    dep_id = await _seed_webhook_dep(app_engine, t, u, w)
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await rotate_key(
                dep_id=dep_id, request=_StubRequest(), ctx=ctx, session=s,
                service=_AllowAuthz(),
            )
        assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_rotate_key_404_for_unknown(pg_engine, app_engine):
    """rotate-key on an unknown id → 404."""
    from fastapi import HTTPException

    from vibecanvas_api.routes.deployments import rotate_key
    from vibecanvas_api.storage.db import session_scope

    t, u, _ = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    ctx = _StubCtx(t, u)
    async with session_scope(tenant_id=str(t)) as s:
        with pytest.raises(HTTPException) as exc:
            await rotate_key(
                dep_id=uuid.uuid4(), request=_StubRequest(), ctx=ctx,
                session=s, service=_AllowAuthz(),
            )
        assert exc.value.status_code == 404


# -------------------------------------------------- workflow-delete guard


@pytest.mark.asyncio
async def test_workflow_delete_guard_blocks_when_enabled_dep_exists(
    pg_engine, app_engine,
):
    """Spec §10.4 — DELETE /workflows/{wf_id} 409s while any enabled,
    non-soft-deleted deployment references the workflow.

    We exercise the actual ``delete_workflow`` handler with a duck-typed
    repo (we only need to confirm the guard fires BEFORE the repo
    delete) — and equally critically verify the underlying SQL query
    by running it directly on the live row, so a refactor that drops
    the guard query would break this test.
    """
    from fastapi import HTTPException

    from vibecanvas_api.routes.workflows import delete_workflow
    from vibecanvas_api.storage.db import session_scope

    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    await _seed_api_dep(app_engine, t, u, w)  # enabled by default

    # 1) Direct-SQL: the guard query finds the live deployment.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t)},
        )
        in_use = (await c.execute(
            text(
                "SELECT 1 FROM deployments "
                "WHERE wf_id = :wf AND enabled = TRUE "
                "AND deleted_at IS NULL LIMIT 1"
            ),
            {"wf": w},
        )).one_or_none()
    assert in_use is not None, (
        "Guard query should find the enabled deployment"
    )

    # 2) Handler-level: the wired guard 409s.
    class _Repo:
        """Stub repo — the handler must 409 BEFORE calling delete_workflow."""

        async def delete_workflow(self, _wf_id):  # noqa: D401
            raise AssertionError(
                "delete_workflow should not be reached when an enabled "
                "deployment references the wf_id"
            )

    async with session_scope(tenant_id=str(t)) as s:
        from types import SimpleNamespace
        from starlette.requests import Request
        from vibecanvas_api.auth.deps import AuthContext
        from vibecanvas_api.authorization.types import Decision

        class _AllowAuthzService:
            async def check(self, *_args, **_kwargs):
                return Decision(True, reason_code="test_allow")

        request = Request({
            "type": "http",
            "method": "DELETE",
            "path": f"/api/v1/workflows/{w}",
            "headers": [],
            "query_string": b"",
            "app": SimpleNamespace(
                state=SimpleNamespace(openfga_client=None),
            ),
            "state": {"request_id": "workflow-delete-guard-test"},
        })
        auth = AuthContext(
            user_id=str(u),
            tenant_id=str(t),
            email="",
            membership_role="owner",
            membership_status="active",
        )
        with pytest.raises(HTTPException) as exc:
            await delete_workflow(
                wf_id=w,
                request=request,
                ctx=auth,
                repo=_Repo(),
                session=s,
                service=_AllowAuthzService(),
            )
        assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_workflow_delete_guard_allows_when_only_disabled_dep(
    pg_engine, app_engine,
):
    """Disabled deployments don't block delete (the guard filters by
    ``enabled = TRUE``).

    This is the inverse-direction sanity check — without it, the guard
    could over-restrict and break the "disable then delete" workflow
    documented in the user-facing 409 message."""
    t, u, w = await _seed_minimal_tenant_user_wf(pg_engine, app_engine)
    # Seed a deployment but DISABLE it.
    dep_id, _ = await _seed_api_dep(app_engine, t, u, w)
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t)},
        )
        await c.execute(
            text("UPDATE deployments SET enabled = FALSE WHERE id = :id"),
            {"id": dep_id},
        )
        await c.commit()
    # The guard query MUST now return nothing.
    async with app_engine.connect() as c:
        await c.execute(
            text("SELECT set_config('app.tenant_id', :t, false)"),
            {"t": str(t)},
        )
        in_use = (await c.execute(
            text(
                "SELECT 1 FROM deployments "
                "WHERE wf_id = :wf AND enabled = TRUE "
                "AND deleted_at IS NULL LIMIT 1"
            ),
            {"wf": w},
        )).one_or_none()
    assert in_use is None, (
        "Guard query should NOT match a disabled deployment"
    )
