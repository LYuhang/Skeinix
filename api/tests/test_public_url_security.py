"""SSRF regression tests for user-controlled outbound URL validation."""
from __future__ import annotations

import socket

import httpx
import pytest
from vibecanvas_api.services import pinned_http
from vibecanvas_api.services.pinned_http import request_pinned_public_url
from vibecanvas_api.services.public_url import (
    PublicUrlError,
    validate_public_http_url,
)


def _answers(*addresses: str):
    return [
        (
            socket.AF_INET6 if ":" in address else socket.AF_INET,
            socket.SOCK_STREAM,
            6,
            "",
            (address, 443, 0, 0) if ":" in address else (address, 443),
        )
        for address in addresses
    ]


@pytest.mark.asyncio
async def test_public_https_url_returns_validated_dns_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers("93.184.216.34", "2606:2800:220:1::"),
    )
    target = await validate_public_http_url("https://Example.COM/mcp")
    assert target.hostname == "example.com"
    assert target.port == 443
    assert target.addresses == ("93.184.216.34", "2606:2800:220:1::")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "169.254.169.254",
        "192.0.2.1",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
    ],
)
async def test_private_local_and_reserved_answers_are_rejected(
    monkeypatch, address,
):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers(address),
    )
    with pytest.raises(PublicUrlError, match="must not resolve"):
        await validate_public_http_url("https://attacker.example/mcp")


@pytest.mark.asyncio
async def test_mixed_public_private_dns_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers("93.184.216.34", "127.0.0.1"),
    )
    with pytest.raises(PublicUrlError, match="must not resolve"):
        await validate_public_http_url("https://rebinding.example/mcp")


@pytest.mark.asyncio
async def test_operator_trusted_proxy_dns_answer_is_allowed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers("198.18.0.9"),
    )
    target = await validate_public_http_url(
        "https://mcp.example/mcp",
        trusted_proxy_cidrs=("198.18.0.0/15",),
    )
    assert target.addresses == ("198.18.0.9",)


@pytest.mark.asyncio
async def test_trusted_proxy_range_does_not_allow_other_private_answers(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers("10.1.2.3"),
    )
    with pytest.raises(PublicUrlError, match="must not resolve"):
        await validate_public_http_url(
            "https://attacker.example/mcp",
            trusted_proxy_cidrs=("198.18.0.0/15",),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/mcp",
        "file:///etc/passwd",
        "https://user:secret@example.com/mcp",
        "https://localhost/mcp",
        "https://api.localhost/mcp",
    ],
)
async def test_unsafe_url_shapes_are_rejected(url):
    with pytest.raises(PublicUrlError):
        await validate_public_http_url(url)


@pytest.mark.asyncio
async def test_pinned_request_supports_redirects_without_dns_rebinding(
    monkeypatch,
):
    dns = {
        "origin.example": "93.184.216.34",
        "cdn.example": "142.250.72.14",
    }
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *_args, **_kwargs: _answers(dns[host]),
    )
    pinned_destinations = []

    def transport_factory(*, addresses, **_kwargs):
        pinned_destinations.append(addresses)

        async def handler(request):
            if request.url.host == "origin.example":
                return httpx.Response(
                    302,
                    headers={"location": "https://cdn.example/image.png"},
                )
            return httpx.Response(200, content=b"image")

        return httpx.MockTransport(handler)

    monkeypatch.setattr(
        pinned_http,
        "PinnedAsyncHTTPTransport",
        transport_factory,
    )
    response = await request_pinned_public_url(
        "GET",
        "https://origin.example/start",
        allow_redirects=True,
    )
    assert response.content == b"image"
    assert pinned_destinations == [
        {"origin.example": ("93.184.216.34",)},
        {"cdn.example": ("142.250.72.14",)},
    ]


@pytest.mark.asyncio
async def test_pinned_request_supports_operator_trusted_proxy_dns(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers("198.18.0.9"),
    )
    pinned_destinations = []

    def transport_factory(*, addresses, **_kwargs):
        pinned_destinations.append(addresses)
        return httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"ok")
        )

    monkeypatch.setattr(pinned_http, "PinnedAsyncHTTPTransport", transport_factory)
    response = await request_pinned_public_url(
        "GET",
        "https://catalog.example/servers",
        trusted_proxy_cidrs=("198.18.0.0/15",),
    )
    assert response.content == b"ok"
    assert pinned_destinations == [{"catalog.example": ("198.18.0.9",)}]


@pytest.mark.asyncio
async def test_pinned_request_forwards_explicit_operator_proxy(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: _answers("198.18.0.9"),
    )
    transport_arguments = []

    def transport_factory(*, addresses, proxy=None, **_kwargs):
        transport_arguments.append((addresses, proxy))
        return httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"ok")
        )

    monkeypatch.setattr(pinned_http, "PinnedAsyncHTTPTransport", transport_factory)
    response = await request_pinned_public_url(
        "GET",
        "https://catalog.example/servers",
        trusted_proxy_cidrs=("198.18.0.0/15",),
        proxy="http://proxy.internal:7897",
    )

    assert response.content == b"ok"
    assert transport_arguments == [(
        {"catalog.example": ("198.18.0.9",)},
        "http://proxy.internal:7897",
    )]


@pytest.mark.asyncio
async def test_pinned_request_rejects_private_redirect_hop(monkeypatch):
    def resolve(host, *_args, **_kwargs):
        address = "93.184.216.34" if host == "origin.example" else "169.254.169.254"
        return _answers(address)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    transport_count = 0

    def transport_factory(*, addresses, **_kwargs):
        nonlocal transport_count
        transport_count += 1

        async def handler(_request):
            return httpx.Response(
                302,
                headers={"location": "https://metadata.example/latest"},
            )

        return httpx.MockTransport(handler)

    monkeypatch.setattr(
        pinned_http,
        "PinnedAsyncHTTPTransport",
        transport_factory,
    )
    with pytest.raises(PublicUrlError, match="must not resolve"):
        await request_pinned_public_url(
            "GET",
            "https://origin.example/start",
            allow_redirects=True,
        )
    assert transport_count == 1
