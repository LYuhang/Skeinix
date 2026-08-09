"""Extract IP, user-agent, and request ID for audit records."""
from __future__ import annotations

from dataclasses import dataclass
import ipaddress

from vibecanvas_api.config import config
from vibecanvas_api.observability import context as obs_context


@dataclass(frozen=True)
class AuditContext:
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None


def _trusted_client_ip(
    peer: str | None,
    forwarded_for: str | None,
    trusted_proxy_cidrs: tuple[str, ...],
) -> str | None:
    """Resolve a client IP without trusting headers from an untrusted peer."""
    if not peer:
        return None
    try:
        peer_address = ipaddress.ip_address(peer)
        trusted_networks = tuple(
            ipaddress.ip_network(value, strict=False)
            for value in trusted_proxy_cidrs
        )
    except ValueError:
        return peer
    if not forwarded_for or not any(
        peer_address in network for network in trusted_networks
    ):
        return str(peer_address)

    chain: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        chain = [
            ipaddress.ip_address(value.strip())
            for value in forwarded_for.split(",")
            if value.strip()
        ]
    except ValueError:
        return str(peer_address)
    # Work from the trusted socket peer toward the client. The first address
    # outside the configured proxy boundary is authoritative; left-most values
    # beyond it may have been supplied by the client and are ignored.
    for address in reversed(chain):
        if not any(address in network for network in trusted_networks):
            return str(address)
    return str(chain[0]) if chain else str(peer_address)


def extract_request_audit_context(
    request,
    *,
    trusted_proxy_cidrs: tuple[str, ...] | None = None,
) -> AuditContext:
    """Pull a proxy-safe IP, user agent, and observability request id."""
    headers = request.headers
    peer = request.client.host if getattr(request, "client", None) else None
    ip = _trusted_client_ip(
        peer,
        headers.get("X-Forwarded-For"),
        config.trusted_proxy_cidrs
        if trusted_proxy_cidrs is None
        else trusted_proxy_cidrs,
    )
    return AuditContext(
        ip_address=ip,
        user_agent=headers.get("User-Agent"),
        request_id=obs_context.get_request_id(),
    )
