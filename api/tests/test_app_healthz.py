"""Smoke: app builds, /healthz returns 200 OK, CORS origins from env."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_build_app_and_healthz():
    from vibecanvas_api.app import build_app
    client = TestClient(build_app())
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_cors_origins_default(monkeypatch):
    monkeypatch.delenv("VIBECANVAS_API_CORS_ORIGINS", raising=False)
    from vibecanvas_api.app import _parse_cors_origins
    assert _parse_cors_origins() == ["http://localhost:3000"]


def test_cors_origins_env_overrides(monkeypatch):
    monkeypatch.setenv(
        "VIBECANVAS_API_CORS_ORIGINS",
        "https://app.example.com,https://staging.example.com",
    )
    from vibecanvas_api.app import _parse_cors_origins
    assert _parse_cors_origins() == [
        "https://app.example.com", "https://staging.example.com",
    ]
