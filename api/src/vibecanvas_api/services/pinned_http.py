"""HTTP transport that dials only pre-validated DNS answers.

TLS still receives the original hostname for SNI and certificate validation;
only the TCP dial address is substituted.  This closes the validation/connect
DNS-rebinding window for user-controlled outbound destinations.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urljoin

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend

from vibecanvas_api.services.public_url import (
    PublicUrlError,
    validate_public_http_url,
)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, addresses: dict[str, tuple[str, ...]]) -> None:
        self._addresses = {
            host.rstrip(".").casefold(): tuple(values)
            for host, values in addresses.items()
        }
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        candidates = self._addresses.get(str(host).rstrip(".").casefold(), (host,))
        last_error: BaseException | None = None
        for address in candidates:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except Exception as exc:  # noqa: BLE001 -- try each validated address
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError("pinned HTTP destination is unavailable")

    async def connect_unix_socket(self, path: str, **kwargs):
        raise OSError("Unix socket HTTP destinations are forbidden")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self,
        *,
        addresses: dict[str, tuple[str, ...]],
        proxy: str | None = None,
        max_connections: int = 2,
    ) -> None:
        super().__init__(
            proxy=proxy,
            trust_env=False,
            http1=True,
            http2=False,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=0,
            ),
        )
        # httpx does not expose a DNS-pinning constructor argument, while its
        # pinned httpcore dependency does. Replace the backend before first
        # use; TLS still receives the original hostname from the pool origin.
        self._pool._network_backend = PinnedNetworkBackend(addresses)  # type: ignore[attr-defined]


async def request_pinned_public_url(
    method: str,
    url: str,
    *,
    label: str = "URL",
    timeout: httpx.Timeout | float = 15.0,
    headers: dict[str, str] | None = None,
    allow_redirects: bool = False,
    max_redirects: int = 4,
    max_response_bytes: int = 8 * 1024 * 1024,
    trusted_proxy_cidrs: Iterable[str] = (),
    proxy: str | None = None,
    **request_kwargs,
) -> httpx.Response:
    """Validate and dial exactly the validated public DNS answers.

    Redirects can be retained for compatible read-only integrations, but each
    destination is resolved, validated, and pinned as a new hop. Response size
    is bounded while streaming so metadata endpoints cannot exhaust memory.
    """
    if max_response_bytes <= 0:
        raise ValueError("max_response_bytes must be positive")
    current_url = str(url or "")
    visited: set[str] = set()
    for hop in range(max_redirects + 1):
        target = await validate_public_http_url(
            current_url,
            label=label,
            require_https=True,
            trusted_proxy_cidrs=trusted_proxy_cidrs,
        )
        if target.url in visited:
            raise PublicUrlError(f"{label} redirect loop detected")
        visited.add(target.url)
        transport = PinnedAsyncHTTPTransport(
            addresses={target.hostname: target.addresses},
            proxy=proxy,
        )
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client, client.stream(
            method,
            target.url,
            headers=headers,
            **request_kwargs,
        ) as upstream:
            if upstream.is_redirect:
                location = upstream.headers.get("location")
                if not allow_redirects:
                    raise PublicUrlError(f"{label} redirects are not accepted")
                if not location:
                    raise PublicUrlError(f"{label} returned an invalid redirect")
                if hop >= max_redirects:
                    raise PublicUrlError(f"{label} redirected too many times")
                current_url = urljoin(target.url, location)
                continue
            declared_size = int(upstream.headers.get("content-length") or 0)
            if declared_size > max_response_bytes:
                raise PublicUrlError(f"{label} response is too large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in upstream.aiter_bytes():
                total += len(chunk)
                if total > max_response_bytes:
                    raise PublicUrlError(f"{label} response is too large")
                chunks.append(chunk)
            return httpx.Response(
                status_code=upstream.status_code,
                headers=upstream.headers,
                content=b"".join(chunks),
                request=upstream.request,
                extensions=upstream.extensions,
            )
    raise PublicUrlError(f"{label} redirected too many times")


__all__ = [
    "PinnedAsyncHTTPTransport",
    "PinnedNetworkBackend",
    "request_pinned_public_url",
]
