"""Response security headers shared by API, SSE, and mounted MCP transports."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Any


Header = tuple[bytes, bytes]
Send = Callable[[dict[str, Any]], Awaitable[None]]

_COMMON_HEADERS: tuple[Header, ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (
        b"permissions-policy",
        b"camera=(), microphone=(), geolocation=(), payment=(), usb=(), "
        b"serial=(), bluetooth=(), browsing-topics=()",
    ),
    (b"x-frame-options", b"DENY"),
)
_PRODUCTION_HEADERS: tuple[Header, ...] = (
    (b"strict-transport-security", b"max-age=31536000; includeSubDomains"),
    (
        b"content-security-policy",
        b"default-src 'none'; base-uri 'none'; object-src 'none'; "
        b"frame-ancestors 'none'; form-action 'none'",
    ),
)
_SENSITIVE_PREFIXES = (
    "/api/v1/auth",
    "/api/v1/llm-credentials",
    "/api/v1/mcp-servers",
    "/api/v1/agent-runtime",
)


def _append_missing(headers: list[Header], additions: Iterable[Header]) -> None:
    existing = {name.lower() for name, _value in headers}
    for name, value in additions:
        if name not in existing:
            headers.append((name, value))
            existing.add(name)


class SecurityHeadersMiddleware:
    """Pure ASGI middleware so streaming responses remain unbuffered."""

    def __init__(self, app, *, production: bool):
        self.app = app
        self.production = production

    async def __call__(self, scope, receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers") or [])
                _append_missing(headers, _COMMON_HEADERS)
                if self.production:
                    _append_missing(headers, _PRODUCTION_HEADERS)
                if path.startswith(_SENSITIVE_PREFIXES):
                    _append_missing(headers, ((b"cache-control", b"no-store"),))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
