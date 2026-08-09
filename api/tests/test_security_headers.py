from __future__ import annotations

import asyncio

from vibecanvas_api.security_headers import SecurityHeadersMiddleware


async def _response_headers(*, production: bool, path: str, supplied=()):
    async def app(_scope, _receive, send):
        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": list(supplied),
        })
        await send({"type": "http.response.body", "body": b"ok"})

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    middleware = SecurityHeadersMiddleware(app, production=production)
    await middleware(
        {"type": "http", "path": path, "method": "GET", "headers": []},
        receive,
        send,
    )
    return dict(messages[0]["headers"])


def test_common_headers_are_streaming_safe_and_sensitive_routes_do_not_cache():
    headers = asyncio.run(_response_headers(
        production=False,
        path="/api/v1/auth/me",
    ))
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"referrer-policy"] == b"no-referrer"
    assert headers[b"cache-control"] == b"no-store"
    assert b"content-security-policy" not in headers


def test_production_adds_hsts_and_does_not_override_route_csp():
    headers = asyncio.run(_response_headers(
        production=True,
        path="/oauth/callback",
        supplied=((b"content-security-policy", b"script-src 'nonce-route'"),),
    ))
    assert headers[b"strict-transport-security"].startswith(b"max-age=")
    assert headers[b"content-security-policy"] == b"script-src 'nonce-route'"
