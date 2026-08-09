"""Validation helpers for user-controlled outbound HTTP(S) destinations.

The caller receives both the normalized URL and the DNS answers that were
validated.  Consumers that open a socket themselves should dial one of the
returned addresses instead of resolving the hostname a second time; that is
what closes the DNS-rebinding window between validation and connect.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit


class PublicUrlError(ValueError):
    """Raised when a user URL is not a public, directly routable destination."""


@dataclass(frozen=True, slots=True)
class PublicUrlTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(address.is_global)


async def validate_public_http_url(
    value: str,
    *,
    label: str = "URL",
    require_https: bool = True,
    trusted_proxy_cidrs: Iterable[str] = (),
) -> PublicUrlTarget:
    """Resolve and validate a public HTTP(S) URL.

    Credentials in the authority, local/private/link-local/documentation
    addresses, Unix-socket pseudo URLs, and unresolved hosts are rejected.
    Every DNS answer must be public: accepting a mixed public/private answer
    would let the downstream client select the private address.
    """

    raw = str(value or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise PublicUrlError(f"{label} is malformed") from exc

    allowed_schemes = {"https"} if require_https else {"http", "https"}
    if parts.scheme.lower() not in allowed_schemes:
        expected = "HTTPS" if require_https else "HTTP(S)"
        raise PublicUrlError(f"{label} must be an absolute public {expected} URL")
    if (
        not parts.hostname
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
    ):
        raise PublicUrlError(
            f"{label} must be an absolute public URL without embedded credentials"
        )

    hostname = parts.hostname.rstrip(".").lower()
    if not hostname or hostname == "localhost" or hostname.endswith(".localhost"):
        raise PublicUrlError(f"{label} must not target localhost")
    resolved_port = port or (443 if parts.scheme.lower() == "https" else 80)

    try:
        answers = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: socket.getaddrinfo(
                hostname,
                resolved_port,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            ),
        )
    except socket.gaierror as exc:
        raise PublicUrlError(f"{label} host could not be resolved") from exc

    addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
    if not addresses:
        raise PublicUrlError(f"{label} host returned no usable addresses")
    try:
        trusted_proxy_networks = tuple(
            ipaddress.ip_network(value, strict=True)
            for value in trusted_proxy_cidrs
        )
    except ValueError as exc:
        raise PublicUrlError(f"{label} trusted proxy range is invalid") from exc

    def is_allowed(address_value: str) -> bool:
        if _is_public_address(address_value):
            return True
        try:
            address = ipaddress.ip_address(address_value)
        except ValueError:
            return False
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        return any(address in network for network in trusted_proxy_networks)

    if any(not is_allowed(address) for address in addresses):
        raise PublicUrlError(
            f"{label} must not resolve to a private, local, reserved, or "
            "non-routable address"
        )

    return PublicUrlTarget(
        url=parts.geturl(),
        hostname=hostname,
        port=resolved_port,
        addresses=addresses,
    )
