"""The application boots through its full real lifespan.

Exercises ``build_app()``'s real lifespan: alembic ``upgrade head``
(offloaded to a worker thread) + the psycopg ``AsyncConnectionPool`` +
``AsyncPostgresSaver.setup()`` + singleton wiring + agent warm-build,
then serves a request and shuts the lifespan down cleanly.

The session-scoped ``_migrate`` fixture (autouse) already migrated the
pytest-postgresql DB and — critically — mutated the live ``config``
singleton's ``database.url`` to the test DB in-memory (conftest T4
fix). So the lifespan's own migration step re-runs ``upgrade head``
against the SAME test DB; it is idempotent (alembic_version table), no
double-DDL. ``monkeypatch.setenv`` is kept (plan intent) for any
subprocess / late importer but is not what makes the URL correct here.

Using ``fastapi.testclient.TestClient`` as a context manager runs the
real Starlette lifespan (startup on ``__enter__``, shutdown on
``__exit__``) — the SAME mechanism the frozen ``test_routes_*`` tests
rely on. The plan's verbatim ``AsyncClient`` + nested ``async with c``
form is dropped: ``httpx.ASGITransport`` does NOT run the lifespan and
the nested re-enter raises on this httpx version (documented adaptation;
the intent — "app boots through the full real lifespan and serves a
request" — is preserved exactly).
"""

from __future__ import annotations

import logging

from fastapi.testclient import TestClient


def test_app_boots_and_healthz(pg_url, _migrate, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg_url)

    class _OpenFgaProbe:
        async def probe(self):
            return None

        async def close(self):
            return None

    monkeypatch.setattr(
        "vibecanvas_api.authorization.openfga_client.OpenFgaHttpClient",
        lambda **_kwargs: _OpenFgaProbe(),
    )
    from vibecanvas_api.app import build_app

    with TestClient(build_app()) as c:
        # Assert at the serving boundary. TestClient/pytest may restore its own
        # process-global logging state while the lifespan shuts down.
        assert logging.getLogger("vibecanvas_api").isEnabledFor(logging.INFO)
        r = c.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
