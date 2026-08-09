# -*- coding: utf-8 -*-
"""Sandbox-egress B2 — the HOST-SIDE egress broker.

In prod "proxy" mode the sandbox runs with NO direct network. An in-sandbox
forward proxy (sibling task B3) tunnels each outbound HTTP(S) connection over a
bind-mounted host UNIX socket to THIS broker, which:

  1. enforces a per-run public/private destination policy,
  2. dials the real target on the host's network, and
  3. pipes bytes both ways until either side EOFs.

Dev is unaffected — this only activates when the provider runs in proxy mode.

Per-connection wire protocol (the SINGLE source; B3's in-sandbox proxy MUST
mirror it byte-for-byte):

  * Header frame: a 4-byte big-endian unsigned length prefix + that many bytes
    of UTF-8 JSON ``{"host": str, "port": int}``. This REUSES the framing scheme
    from :mod:`vibecanvas_engine.sandbox_bus` (``_LEN = struct.Struct(">I")`` +
    JSON body) — we import :func:`read_frame` from there so there is exactly ONE
    framing implementation and no drift (same contract as ``bus_broker.py``).
  * Status byte (broker → proxy): ``b"\\x01"`` = OK (dial succeeded / allowed),
    ``b"\\x00"`` = DENY (host not on the allowlist).
  * On OK: raw bidirectional byte relay between the proxy connection and the
    dialed target until either side EOFs.

Each accepted UDS connection corresponds to exactly ONE tunneled outbound.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import socket
import threading
from uuid import uuid4

import structlog

# REUSE the engine framing: one 4-byte big-endian length prefix + a JSON body.
# ``read_frame`` returns the decoded dict (or None at clean EOF). Importing it
# (api already depends on engine) keeps the wire format single-sourced — B3 must
# mirror this exact scheme.
from vibecanvas_engine.sandbox_bus import read_frame

logger = structlog.get_logger(__name__)

# Status bytes on the proxy↔broker control channel.
_STATUS_OK = b"\x01"
_STATUS_DENY = b"\x00"

# Bound on dialing the real target so a hung/blackholed host can't pin a relay
# task forever before the relay even starts.
_CONNECT_TIMEOUT = 10.0

# Generous overall guard on the relay so a stuck (never-EOF) tunnel is reaped.
_RELAY_TIMEOUT = 3600.0

# Read chunk for the byte pump.
_CHUNK = 65536

# Cap the per-connection HEADER frame length. The header is a tiny JSON
# ``{"host": str, "port": int}`` — a few hundred bytes at most. Capping the
# declared length (passed to ``read_frame``) means a compromised in-sandbox
# workload can't send a giant length prefix to make THIS host broker allocate a
# huge buffer (memory DoS) before the header is even parsed. The relay byte pump
# (after the header) is unaffected — it reads fixed ``_CHUNK`` slices, never a
# peer-declared length.
_MAX_HEADER_LEN = 65536


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(address.is_global)


async def _resolve_public_addresses(
    host: str,
    port: int,
    trusted_proxy_networks: tuple[
        ipaddress.IPv4Network | ipaddress.IPv6Network, ...
    ] = (),
) -> tuple[str, ...]:
    """Resolve once, reject private answers, and return pinned addresses.

    Some VPN and transparent-proxy products return synthetic non-global IPs for
    public DNS names. Operators may trust only those dedicated CIDRs. A literal
    non-global IP is never promoted by this mechanism, which keeps direct SSRF
    requests blocked.
    """
    answers = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: socket.getaddrinfo(
            host,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        ),
    )
    addresses = tuple(dict.fromkeys(str(answer[4][0]) for answer in answers))
    try:
        literal_host = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal_host = None
    if literal_host is not None and not _is_public_address(str(literal_host)):
        return ()
    if not addresses:
        return ()
    for value in addresses:
        if _is_public_address(value):
            continue
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return ()
        if not any(address in network for network in trusted_proxy_networks):
            return ()
    return addresses


class EgressBroker:
    """Host-side asyncio UNIX-socket server. One accepted connection = one
    tunneled outbound.

    ``allow_hosts`` entries are normalized to lowercase at construction. A
    leading-dot entry (``.example.com``) is a suffix rule that also matches the
    bare apex (``example.com``); any other entry is an exact (case-insensitive)
    match. An EMPTY allowlist denies everything (fail-closed).
    """

    def __init__(
        self,
        socket_path: str,
        *,
        allow_hosts: set[str],
        run_id: str,
        allow_public: bool = False,
        allow_private_targets: set[tuple[str, int]] | None = None,
        trusted_proxy_cidrs: set[str] | None = None,
    ):
        self.socket_path = socket_path
        self.run_id = run_id
        # Normalize to lowercase once; matching is then a plain comparison.
        self._allow_hosts = {h.lower() for h in allow_hosts}
        # Resident sandboxes reuse one broker across multiple jobs. Dynamic
        # authority is therefore represented as scoped leases instead of being
        # merged permanently into the broker's baseline policy. Concurrent jobs
        # temporarily contribute the union of their hosts; releasing the last
        # lease removes that authority again.
        self._host_leases: dict[str, frozenset[str]] = {}
        self._policy_lock = threading.Lock()
        self._allow_public = allow_public
        self._allow_private_targets = {
            (host.lower(), port)
            for host, port in (allow_private_targets or set())
        }
        self._trusted_proxy_networks = tuple(
            ipaddress.ip_network(value, strict=True)
            for value in (trusted_proxy_cidrs or set())
        )
        self._server: "asyncio.AbstractServer | None" = None

    # ---- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Bind + listen on the pathname UDS (async accept). Creates the parent
        dir, unlinks any stale socket, and chmods the socket to ``0o600`` so only
        the owner can connect."""
        run_dir = os.path.dirname(self.socket_path)
        if run_dir:
            os.makedirs(run_dir, exist_ok=True)
        # Pathname socket: a stale file at the path blocks bind. Unlink it.
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        self._server = await asyncio.start_unix_server(
            self._handle, path=self.socket_path
        )
        try:
            os.chmod(self.socket_path, 0o600)
        except OSError:
            logger.warning(
                "egress_socket_chmod_failed",
                path=self.socket_path,
                run_id=self.run_id,
                exc_info=True,
            )

    async def aclose(self) -> None:
        """Stop the server, unlink the socket, rmtree the per-run parent dir.
        Best-effort + idempotent (safe on an already-closed broker)."""
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass
            self._server = None
        try:
            os.unlink(self.socket_path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning(
                "egress_socket_unlink_failed",
                path=self.socket_path,
                run_id=self.run_id,
                exc_info=True,
            )
        run_dir = os.path.dirname(self.socket_path)
        if run_dir:
            shutil.rmtree(run_dir, ignore_errors=True)

    # ---- allowlist ---------------------------------------------------------

    def _host_allowed(self, host: str) -> bool:
        """True iff ``host`` is permitted by the (lowercased) allowlist.

        Exact case-insensitive match, OR suffix match for a leading-dot entry
        (``.example.com`` allows ``a.example.com`` AND the bare ``example.com``).
        Empty allowlist → deny all (fail-closed)."""
        with self._policy_lock:
            allow_public = self._allow_public
            allow_hosts = tuple(
                self._allow_hosts.union(
                    *(set(hosts) for hosts in self._host_leases.values())
                )
            )
        if allow_public:
            return True
        if not allow_hosts:
            return False
        h = host.lower()
        for entry in allow_hosts:
            if entry.startswith("."):
                # ``.example.com`` → match ``*.example.com`` and the apex.
                if h.endswith(entry) or h == entry[1:]:
                    return True
            elif h == entry:
                return True
        return False

    def acquire_allow_hosts(self, hosts: set[str]) -> str | None:
        """Create a scoped host-policy lease for one resident operation."""
        normalized = frozenset(
            host.strip().lower() for host in hosts if host.strip()
        )
        if not normalized:
            return None
        lease_id = uuid4().hex
        with self._policy_lock:
            self._host_leases[lease_id] = normalized
        return lease_id

    def release_allow_hosts(self, lease_id: str | None) -> None:
        """Revoke one scoped lease; idempotent for teardown paths."""
        if not lease_id:
            return
        with self._policy_lock:
            self._host_leases.pop(lease_id, None)

    # ---- per-connection handler -------------------------------------------

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Handle ONE tunneled outbound. Any per-connection error is caught here
        so it can never crash the server."""
        try:
            try:
                header = await read_frame(reader, max_len=_MAX_HEADER_LEN)
            except ValueError:
                # Oversized / malformed header length prefix → reject WITHOUT the
                # readexactly(length) allocation. Drop the connection (no status
                # byte, no dial). Never relayed.
                logger.info("egress_header_oversized", run_id=self.run_id)
                return
            if not isinstance(header, dict):
                return  # EOF / malformed before a header → nothing to do.
            host = header.get("host")
            port = header.get("port")
            if not isinstance(host, str) or not isinstance(port, int):
                return
            if not 1 <= port <= 65535:
                writer.write(_STATUS_DENY)
                await writer.drain()
                return

            private_target_allowed = (
                host.lower(), port
            ) in self._allow_private_targets
            if not private_target_allowed and not self._host_allowed(host):
                writer.write(_STATUS_DENY)
                try:
                    await writer.drain()
                except Exception:
                    pass
                # HOST ONLY in the log — never the payload/secrets.
                logger.info("egress_denied", host=host, run_id=self.run_id)
                return

            # Public destinations are resolved once, required to contain only
            # global addresses, and dialled by pinned IP. Trusted private
            # destinations are exact operator-owned host:port grants (for
            # example the internal Platform MCP origin), never wildcard rules.
            try:
                if private_target_allowed:
                    target_r, target_w = await asyncio.wait_for(
                        asyncio.open_connection(host, port),
                        timeout=_CONNECT_TIMEOUT,
                    )
                else:
                    resolve = (
                        _resolve_public_addresses(host, port)
                        if not self._trusted_proxy_networks
                        else _resolve_public_addresses(
                            host,
                            port,
                            self._trusted_proxy_networks,
                        )
                    )
                    addresses = await asyncio.wait_for(
                        resolve, timeout=_CONNECT_TIMEOUT
                    )
                    if not addresses:
                        raise OSError("destination did not resolve exclusively public")
                    last_error: BaseException | None = None
                    target_r = target_w = None
                    for address in addresses:
                        try:
                            target_r, target_w = await asyncio.wait_for(
                                asyncio.open_connection(address, port),
                                timeout=_CONNECT_TIMEOUT,
                            )
                            break
                        except Exception as exc:
                            last_error = exc
                    if target_r is None or target_w is None:
                        raise OSError(
                            "all validated destination addresses failed"
                        ) from last_error
            except Exception:
                writer.write(_STATUS_DENY)
                try:
                    await writer.drain()
                except Exception:
                    pass
                logger.info(
                    "egress_connect_failed",
                    host=host,
                    port=port,
                    run_id=self.run_id,
                )
                return

            writer.write(_STATUS_OK)
            try:
                await writer.drain()
            except Exception:
                pass

            try:
                await asyncio.wait_for(
                    self._relay(reader, writer, target_r, target_w),
                    timeout=_RELAY_TIMEOUT,
                )
            finally:
                _safe_close(target_w)
        except Exception:
            # Never let one connection take down the listener.
            logger.warning(
                "egress_connection_error", run_id=self.run_id, exc_info=True
            )
        finally:
            _safe_close(writer)

    async def _relay(
        self,
        client_r: asyncio.StreamReader,
        client_w: asyncio.StreamWriter,
        target_r: asyncio.StreamReader,
        target_w: asyncio.StreamWriter,
    ) -> None:
        """Bidirectional byte pump until either side EOFs. Two pump tasks; when
        the first finishes (an EOF) we cancel the other and let teardown close
        both writers."""
        c2t = asyncio.create_task(self._pump(client_r, target_w))
        t2c = asyncio.create_task(self._pump(target_r, client_w))
        done, pending = await asyncio.wait(
            {c2t, t2c}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        # Retrieve results/exceptions so neither completed nor cancelled task
        # logs an "exception was never retrieved" warning.
        await asyncio.gather(*done, *pending, return_exceptions=True)

    @staticmethod
    async def _pump(
        src: asyncio.StreamReader, dst: asyncio.StreamWriter
    ) -> None:
        """Copy ``src`` → ``dst`` until EOF; swallow reset/incomplete on teardown."""
        try:
            while True:
                data = await src.read(_CHUNK)
                if not data:
                    break
                dst.write(data)
                await dst.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            # Signal EOF to the peer so its pump also unwinds.
            try:
                if dst.can_write_eof():
                    dst.write_eof()
            except Exception:
                pass


def _safe_close(writer: "asyncio.StreamWriter | None") -> None:
    if writer is None:
        return
    try:
        writer.close()
    except Exception:
        pass
