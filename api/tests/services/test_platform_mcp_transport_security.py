"""Platform MCP accepts only its configured private service authority."""

from __future__ import annotations

import pytest

from vibecanvas_api.services.platform_mcp.server import (
    _platform_transport_security,
)


@pytest.mark.parametrize(
    ("origin", "host", "allowed_origin"),
    [
        ("http://api:8000", "api:8000", "http://api:8000"),
        (
            "http://127.0.0.1:8000/private-prefix",
            "127.0.0.1:8000",
            "http://127.0.0.1:8000",
        ),
        ("https://platform.example", "platform.example", "https://platform.example"),
        ("http://[::1]:8000", "[::1]:8000", "http://[::1]:8000"),
    ],
)
def test_platform_transport_security_uses_exact_configured_authority(
    origin: str,
    host: str,
    allowed_origin: str,
) -> None:
    settings = _platform_transport_security(origin)

    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == [host]
    assert settings.allowed_origins == [allowed_origin]


def test_platform_servers_accept_the_active_internal_service_authority() -> None:
    from vibecanvas_api.services.platform_mcp.server import CONFIG_MCP

    settings = CONFIG_MCP.settings.transport_security

    assert settings is not None
    expected = _platform_transport_security(
        CONFIG_MCP.settings.auth.issuer_url.unicode_string().rstrip("/")
    )
    assert settings.allowed_hosts == expected.allowed_hosts
    assert settings.allowed_origins == expected.allowed_origins
