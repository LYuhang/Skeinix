from __future__ import annotations

from vibecanvas_api.services.sandbox.network_diagnostics import (
    detect_synthetic_dns,
)


class _Connection:
    def close(self):
        return None


def test_detects_normal_public_dns(monkeypatch):
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.network_diagnostics.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    result = detect_synthetic_dns()
    assert result.status == "public-dns"
    assert result.suggested_cidr == ""


def test_suggests_only_known_reachable_fake_ip_range(monkeypatch):
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.network_diagnostics.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("198.18.0.7", 443))],
    )
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.network_diagnostics.socket.create_connection",
        lambda *_args, **_kwargs: _Connection(),
    )
    result = detect_synthetic_dns()
    assert result.status == "known-synthetic-dns"
    assert result.suggested_cidr == "198.18.0.0/15"


def test_unknown_private_dns_is_never_suggested(monkeypatch):
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.network_diagnostics.socket.getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("10.20.30.40", 443))],
    )
    result = detect_synthetic_dns()
    assert result.status == "non-public-dns-untrusted"
    assert result.suggested_cidr == ""
