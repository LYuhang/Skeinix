from __future__ import annotations

from unittest.mock import patch

from starlette.requests import Request

from vibecanvas_api.auth.session_security import cookie_credential
from vibecanvas_api.config import config


def _request(cookie: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/api/v1/auth/me",
            "headers": [(b"cookie", cookie.encode("ascii"))],
            "client": ("203.0.113.10", 443),
            "server": ("app.example.com", 443),
        }
    )


def test_production_ignores_development_cookie_namespace():
    with patch.object(config, "environment", "production"):
        credential = cookie_credential(
            _request(
                "vibecanvas-web-session=attacker-cookie; "
                "vibecanvas-web-csrf=attacker-csrf"
            )
        )
    assert credential is None


def test_production_accepts_only_host_prefixed_cookie_namespace():
    with patch.object(config, "environment", "production"):
        credential = cookie_credential(
            _request(
                "vibecanvas-web-session=ignored; "
                "__Host-vibecanvas-web-session=secure-session; "
                "__Host-vibecanvas-web-csrf=secure-csrf"
            )
        )
    assert credential is not None
    assert credential.raw_session == "secure-session"
    assert credential.raw_csrf == "secure-csrf"


def test_local_extension_accepts_only_secure_partition_namespace():
    with (
        patch.object(config, "environment", "development"),
        patch.object(config, "web_session_cookie_secure", False),
    ):
        credential = cookie_credential(
            _request(
                "vibecanvas-extension-session=ignored; "
                "__Host-vibecanvas-extension-session=extension-session; "
                "__Host-vibecanvas-extension-csrf=extension-csrf"
            )
        )
    assert credential is not None
    assert credential.audience == "extension"
    assert credential.raw_session == "extension-session"
    assert credential.raw_csrf == "extension-csrf"
