"""Dependency overlay HTTP boundary.

``POST /ensure`` is a compatibility rejection and must never install packages;
only an interactive Workflow-page execution may initialize an overlay.
``GET /{overlay_key}`` remains a read-only status endpoint.

Auth: routes REQUIRE a logged-in user (``current_user``) but the data is NOT
tenant-scoped — ``env_builds`` is a global, RLS-free public-PyPI registry, so
the DB access goes through ``session_scope_admin()``. The conftest's superuser
``pg_engine`` is injected as ``db._admin_engine`` so the admin session reads
exactly the rows the test seeds.

"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from vibecanvas_api.services.env.overlay_key import compute_overlay_key
from vibecanvas_api.storage import db as db_mod
from vibecanvas_api.storage.repo_env_builds import EnvBuildsRepo


# ----------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def _inject_admin_engine(pg_engine, monkeypatch):
    """Point ``session_scope_admin`` at the superuser ``pg_engine`` so the
    route's admin session sees the same (RLS-free) ``env_builds`` rows the
    test seeds — mirrors test_deployments_crud's admin-engine monkeypatch."""
    monkeypatch.setattr(db_mod, "_admin_engine", pg_engine)


async def _token(client, email: str) -> str:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "username": "Test User", "password": "pw12345678"},
    )
    return r.json()["session_token"]


def _auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


async def _seed_row(pg_engine, key: str, reqs: str, *, ready=False, failed=None):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        repo = EnvBuildsRepo(s)
        await repo.upsert_building(key, reqs)
        if ready:
            await repo.mark_ready(key)
        if failed is not None:
            await repo.mark_failed(key, failed)
        await s.commit()


# ------------------------------------------------------- retired build route


@pytest.mark.asyncio
async def test_ensure_route_rejects_without_creating_build(client, pg_engine):
    tok = await _token(client, "envs-new@example.com")
    reqs = "pandas==2.1.4"
    r = await client.post(
        "/api/v1/envs/ensure", json={"requirements": reqs}, headers=_auth(tok)
    )
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == (
        "dependencies are prepared by sandboxd only when an authorized "
        "Workflow, Task, or Deployment execution starts"
    )
    key = compute_overlay_key(reqs)
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as s:
        assert await EnvBuildsRepo(s).get(key) is None


@pytest.mark.asyncio
async def test_ensure_route_rejects_even_when_layer_is_ready(client, pg_engine):
    tok = await _token(client, "envs-ready@example.com")
    reqs = "numpy==1.26.4"
    key = compute_overlay_key(reqs)
    await _seed_row(pg_engine, key, reqs, ready=True)

    r = await client.post(
        "/api/v1/envs/ensure", json={"requirements": reqs}, headers=_auth(tok)
    )
    assert r.status_code == 409, r.text


# ------------------------------------------------------------------- status


@pytest.mark.asyncio
async def test_status_found(client, pg_engine):
    tok = await _token(client, "envs-statusfound@example.com")
    reqs = "requests==2.31.0"
    key = compute_overlay_key(reqs)
    await _seed_row(pg_engine, key, reqs, failed="pip: no matching distribution")

    r = await client.get(f"/api/v1/envs/{key}", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overlay_key"] == key
    assert body["status"] == "failed"
    assert body["error_log"] == "pip: no matching distribution"


@pytest.mark.asyncio
async def test_status_unknown(client):
    tok = await _token(client, "envs-statusunknown@example.com")
    key = "f" * 64
    r = await client.get(f"/api/v1/envs/{key}", headers=_auth(tok))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overlay_key"] == key
    assert body["status"] == "unknown"
    assert body["error_log"] is None


# --------------------------------------------------------------------- auth


@pytest.mark.asyncio
async def test_requires_auth(client):
    rp = await client.post(
        "/api/v1/envs/ensure", json={"requirements": "pandas==2.1.4"}
    )
    assert rp.status_code == 401
    rg = await client.get(f"/api/v1/envs/{'a' * 64}")
    assert rg.status_code == 401
