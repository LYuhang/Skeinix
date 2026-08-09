"""Detect dedicated fake-IP DNS ranges used by VPN/transparent proxies."""

from __future__ import annotations

import argparse
import ipaddress
import json
import socket
from dataclasses import asdict, dataclass


_KNOWN_SYNTHETIC_NETWORKS = (ipaddress.ip_network("198.18.0.0/15"),)


@dataclass(frozen=True)
class SyntheticDnsResult:
    host: str
    addresses: tuple[str, ...]
    status: str
    suggested_cidr: str = ""


def detect_synthetic_dns(
    host: str = "example.com",
    *,
    port: int = 443,
    timeout_s: float = 5.0,
) -> SyntheticDnsResult:
    """Identify a known, reachable synthetic range without trusting it."""
    try:
        answers = socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except OSError:
        return SyntheticDnsResult(host, (), "dns-unavailable")
    addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
    if not addresses:
        return SyntheticDnsResult(host, (), "dns-unavailable")

    parsed = tuple(ipaddress.ip_address(value) for value in addresses)
    if all(address.is_global for address in parsed):
        return SyntheticDnsResult(host, addresses, "public-dns")

    for network in _KNOWN_SYNTHETIC_NETWORKS:
        if not all(address in network for address in parsed):
            continue
        reachable = False
        for address in addresses:
            try:
                connection = socket.create_connection(
                    (address, port), timeout=timeout_s
                )
                connection.close()
                reachable = True
                break
            except OSError:
                continue
        if reachable:
            return SyntheticDnsResult(
                host,
                addresses,
                "known-synthetic-dns",
                str(network),
            )
        return SyntheticDnsResult(host, addresses, "synthetic-dns-unreachable")

    return SyntheticDnsResult(host, addresses, "non-public-dns-untrusted")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="example.com")
    parser.add_argument("--suggest-cidr", action="store_true")
    args = parser.parse_args()
    result = detect_synthetic_dns(args.host)
    if args.suggest_cidr:
        print(result.suggested_cidr)
    else:
        print(json.dumps(asdict(result), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
