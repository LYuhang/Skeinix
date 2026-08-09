"""Actual-byte request body limits applied before framework body parsing."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any


Send = Callable[[dict[str, Any]], Awaitable[None]]

_PUBLIC_DEPLOYMENT_PATH_PREFIX = "/api/v1/deployments/"
_PUBLIC_DEPLOYMENT_PATH_SUFFIXES = ("/invoke", "/webhook")
_PUBLIC_DEPLOYMENT_MAX_BYTES = 1024 * 1024


class RequestBodyTooLarge(Exception):
    """Internal control-flow signal; never rendered with request data."""


def _header_value(scope: dict[str, Any], name: bytes) -> bytes | None:
    for key, value in scope.get("headers") or ():
        if key.lower() == name:
            return value
    return None


def _request_limit(path: str, default_limit: int) -> int:
    if path.startswith(_PUBLIC_DEPLOYMENT_PATH_PREFIX) and path.endswith(
        _PUBLIC_DEPLOYMENT_PATH_SUFFIXES
    ):
        return min(default_limit, _PUBLIC_DEPLOYMENT_MAX_BYTES)
    return default_limit


async def _send_too_large(send: Send, limit: int) -> None:
    body = (
        '{"detail":"request body too large","limit_bytes":%d}' % limit
    ).encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP bodies based on received bytes, not headers.

    The receive callable counts chunks as downstream parsers consume them. It
    neither buffers whole requests in memory nor spills plaintext bodies to a
    host temporary file. This gives multipart parsers and authentication
    dependencies a bounded input even when a client omits or lies about
    ``Content-Length``.
    """

    def __init__(self, app, *, default_limit: int):
        self.app = app
        self.default_limit = default_limit

    async def __call__(self, scope, receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        limit = _request_limit(str(scope.get("path") or ""), self.default_limit)
        content_length = _header_value(scope, b"content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except (TypeError, ValueError):
                declared_size = -1
            if declared_size < 0 or declared_size > limit:
                await _send_too_large(send, limit)
                return

        total = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> dict[str, Any]:
            nonlocal total, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                total += len(message.get("body", b""))
                if total > limit:
                    exceeded = True
                    raise RequestBodyTooLarge
            return message

        async def guarded_send(message: dict[str, Any]) -> None:
            nonlocal response_started
            # Starlette's inner exception middleware may try to render the
            # control-flow exception as a 500. Suppress it and let this outer
            # middleware emit the deterministic 413 instead.
            if exceeded:
                return
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except RequestBodyTooLarge:
            pass
        if exceeded and not response_started:
            await _send_too_large(send, limit)
