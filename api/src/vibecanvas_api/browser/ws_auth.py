"""Credential-safe WebSocket handshake contract for browser automation.

Browser WebSocket clients cannot attach an ``Authorization`` header.  Putting a
scoped credential in the request URL is worse: reverse proxies and access logs
commonly retain the URL.  The client therefore offers three WebSocket
subprotocol tokens and the server selects only the public version token:

* ``vibecanvas.browser.v1``
* ``vibecanvas.browser.auth.<scoped-token>``
* ``vibecanvas.browser.id.<browser-id>``

The credential is still protected by TLS and is no longer part of request URLs.
It must be treated like any other authorization header by header-level logging.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


BROWSER_WS_PROTOCOL = "vibecanvas.browser.v1"
_AUTH_PREFIX = "vibecanvas.browser.auth."
_BROWSER_PREFIX = "vibecanvas.browser.id."
_SCOPED_TOKEN_RE = re.compile(r"[A-Za-z0-9._~-]{1,4096}\Z")
_BROWSER_ID_RE = re.compile(r"[A-Za-z0-9._~-]{1,128}\Z")


@dataclass(frozen=True)
class BrowserWsHandshake:
    token: str
    browser_id: str


def build_browser_ws_protocols(token: str, browser_id: str) -> tuple[str, str, str]:
    """Build the ordered client offer; useful for non-browser protocol clients."""
    if _SCOPED_TOKEN_RE.fullmatch(token) is None:
        raise ValueError("invalid browser WebSocket credential")
    if _BROWSER_ID_RE.fullmatch(browser_id) is None:
        raise ValueError("invalid browser identifier")
    return (
        BROWSER_WS_PROTOCOL,
        f"{_AUTH_PREFIX}{token}",
        f"{_BROWSER_PREFIX}{browser_id}",
    )


def parse_browser_ws_protocols(header: str | None) -> BrowserWsHandshake | None:
    """Parse one RFC 6455 ``Sec-WebSocket-Protocol`` offer, fail closed."""
    if not header or len(header) > 8192:
        return None
    offered = [part.strip() for part in header.split(",")]
    if BROWSER_WS_PROTOCOL not in offered:
        return None
    auth_values = [
        part.removeprefix(_AUTH_PREFIX)
        for part in offered
        if part.startswith(_AUTH_PREFIX)
    ]
    browser_values = [
        part.removeprefix(_BROWSER_PREFIX)
        for part in offered
        if part.startswith(_BROWSER_PREFIX)
    ]
    if len(auth_values) != 1 or len(browser_values) != 1:
        return None
    token = auth_values[0]
    browser_id = browser_values[0]
    if _SCOPED_TOKEN_RE.fullmatch(token) is None:
        return None
    if _BROWSER_ID_RE.fullmatch(browser_id) is None:
        return None
    return BrowserWsHandshake(token=token, browser_id=browser_id)


__all__ = [
    "BROWSER_WS_PROTOCOL",
    "BrowserWsHandshake",
    "build_browser_ws_protocols",
    "parse_browser_ws_protocols",
]
