# -*- coding: utf-8 -*-
"""Sandbox-egress B3 — the IN-SANDBOX forward proxy (PURE ENGINE).

In prod "proxy" mode the sandbox has NO direct network. The workload's HTTP
clients are pointed at THIS localhost forward proxy via ``HTTPS_PROXY`` /
``HTTP_PROXY``. ``requests`` and ``httpx`` both honor those env vars, so this
single proxy covers every in-sandbox HTTP client.

For each accepted client connection the proxy:

  1. parses the request head to extract the target ``host:port`` (a ``CONNECT``
     tunnel for HTTPS, or an absolute-form request line / ``Host:`` header for
     plain HTTP),
  2. opens the bind-mounted host UDS and speaks the egress wire protocol to the
     HOST BROKER (:mod:`vibecanvas_api.services.sandbox.egress_broker`), and
  3. on OK, relays bytes bidirectionally between the client and the UDS until
     either side EOFs.

Wire protocol (MUST mirror the broker byte-for-byte; single-sourced in
:mod:`vibecanvas_engine.sandbox_bus`):

  * Header frame: ``encode_frame({"host": str, "port": int})`` — a 4-byte
    big-endian length prefix + UTF-8 JSON body.
  * Status byte (broker → proxy): ``b"\\x01"`` = OK, ``b"\\x00"`` = DENY.
  * On OK: raw bidirectional relay.

Lives in the engine (NO ``vibecanvas_api`` import) so both the engine and the
api sandbox entrypoints can import it (api→engine is the allowed dep direction).
"""

from __future__ import annotations

import asyncio
import os
import threading

import structlog

from vibecanvas_engine.sandbox_bus import encode_frame

logger = structlog.get_logger(__name__)

# Status bytes on the proxy↔broker control channel (mirror the broker).
_STATUS_OK = b"\x01"
_STATUS_DENY = b"\x00"

# Cap on the request head we buffer before parsing — a client that never sends a
# CRLFCRLF must not let us read unboundedly.
_HEAD_CAP = 65536

# Read chunk for the byte pump.
_CHUNK = 65536

# The 403 body sent to the client on a denied / failed tunnel.
_DENY_BODY = b"egress denied"
_DENY_RESPONSE = (
    b"HTTP/1.1 403 Forbidden\r\n"
    b"Content-Type: text/plain\r\n"
    b"Content-Length: " + str(len(_DENY_BODY)).encode("ascii") + b"\r\n"
    b"Connection: close\r\n"
    b"\r\n" + _DENY_BODY
)

_CONNECT_OK = b"HTTP/1.1 200 Connection Established\r\n\r\n"


class EgressProxy:
    """In-sandbox forward proxy. One accepted TCP connection = one tunneled
    outbound through the host UDS."""

    def __init__(self, uds_path: str, *, host: str = "127.0.0.1", port: int):
        self.uds_path = uds_path
        self.host = host
        self.port = port

    # ---- lifecycle ---------------------------------------------------------

    async def serve(self) -> None:
        """Bind + listen on ``host:port`` and serve forever (used when the proxy
        runs on the caller's own event loop)."""
        server = await asyncio.start_server(self._handle, host=self.host, port=self.port)
        async with server:
            await server.serve_forever()

    def start_in_thread(self) -> None:
        """Run the asyncio server in a DAEMON thread and BLOCK until it is
        actually listening, then return.

        The sandbox entrypoint calls this synchronously before running the
        workflow, so the proxy must be accepting connections by the time this
        returns. We set a :class:`threading.Event` from inside the loop once
        ``start_server`` has bound, and wait on it here.
        """
        listening = threading.Event()
        startup_error: list[BaseException] = []

        def _run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _boot() -> None:
                server = await asyncio.start_server(
                    self._handle, host=self.host, port=self.port
                )
                listening.set()
                async with server:
                    await server.serve_forever()

            try:
                loop.run_until_complete(_boot())
            except BaseException as exc:  # surface bind failures to the caller
                startup_error.append(exc)
                listening.set()
            finally:
                try:
                    loop.close()
                except Exception:
                    pass

        thread = threading.Thread(target=_run, name="egress-proxy", daemon=True)
        thread.start()
        listening.wait()
        if startup_error:
            raise startup_error[0]

    # ---- per-connection handler -------------------------------------------

    async def _handle(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        """Handle ONE client connection. Any per-connection error is caught here
        so it can never crash the serve loop."""
        try:
            head = await self._read_head(client_reader)
            if head is None:
                return  # clean EOF / no request line.
            parsed = _parse_head(head)
            if parsed is None:
                await self._reject(client_writer)
                return
            is_connect, host, port = parsed

            try:
                uds_reader, uds_writer = await asyncio.open_unix_connection(
                    self.uds_path
                )
            except Exception:
                logger.warning("egress_proxy_uds_open_failed", exc_info=True)
                await self._reject(client_writer)
                return

            try:
                uds_writer.write(encode_frame({"host": host, "port": port}))
                await uds_writer.drain()
                status = await uds_reader.readexactly(1)
            except Exception:
                _safe_close(uds_writer)
                await self._reject(client_writer)
                return

            if status != _STATUS_OK:
                logger.info("egress_proxy_denied", host=host, port=port)
                _safe_close(uds_writer)
                await self._reject(client_writer)
                return

            try:
                if is_connect:
                    # Acknowledge the tunnel; the client then does its TLS
                    # handshake straight through.
                    client_writer.write(_CONNECT_OK)
                    await client_writer.drain()
                else:
                    # Plain HTTP: the request head we already consumed is part of
                    # the payload — forward it to the target before relaying.
                    uds_writer.write(head)
                    await uds_writer.drain()

                await self._relay(client_reader, client_writer, uds_reader, uds_writer)
            finally:
                _safe_close(uds_writer)
        except Exception:
            logger.warning("egress_proxy_connection_error", exc_info=True)
        finally:
            _safe_close(client_writer)

    async def _read_head(
        self, reader: asyncio.StreamReader
    ) -> "bytes | None":
        """Read the request head up to the first CRLFCRLF (or the cap).

        Tolerant of partial reads: accumulates chunks until the header
        terminator appears. Returns the buffered bytes (which may include some
        body bytes already read for plain HTTP — that's fine, they're forwarded
        verbatim), or ``None`` on a clean EOF with no data."""
        buf = bytearray()
        while True:
            chunk = await reader.read(_CHUNK)
            if not chunk:
                # EOF: return what we have if it's a partial head, else None.
                return bytes(buf) if buf else None
            buf.extend(chunk)
            if b"\r\n\r\n" in buf:
                return bytes(buf)
            if len(buf) >= _HEAD_CAP:
                return bytes(buf)

    async def _reject(self, client_writer: asyncio.StreamWriter) -> None:
        try:
            client_writer.write(_DENY_RESPONSE)
            await client_writer.drain()
        except Exception:
            pass

    async def _relay(
        self,
        client_r: asyncio.StreamReader,
        client_w: asyncio.StreamWriter,
        uds_r: asyncio.StreamReader,
        uds_w: asyncio.StreamWriter,
    ) -> None:
        """Bidirectional byte pump until either side EOFs. Mirrors the broker's
        ``_relay``: two pump tasks, FIRST_COMPLETED, cancel the other."""
        c2u = asyncio.create_task(self._pump(client_r, uds_w))
        u2c = asyncio.create_task(self._pump(uds_r, client_w))
        done, pending = await asyncio.wait(
            {c2u, u2c}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)

    @staticmethod
    async def _pump(src: asyncio.StreamReader, dst: asyncio.StreamWriter) -> None:
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
            try:
                if dst.can_write_eof():
                    dst.write_eof()
            except Exception:
                pass


def maybe_start_egress_proxy() -> "EgressProxy | None":
    """Start the in-sandbox egress proxy IFF the provider signaled proxy mode via
    env (VC_EGRESS_SOCK + VC_EGRESS_PORT). In dev/host-network mode these are
    unset → returns None, starts nothing (byte-identical to today). Fail-soft: if
    the proxy can't bind, log + return None — the workload's HTTP calls will then
    fail (correct fail-closed behavior), not crash the run."""
    sock = os.environ.get("VC_EGRESS_SOCK")
    port = os.environ.get("VC_EGRESS_PORT")
    if not sock or not port:
        return None
    try:
        proxy = EgressProxy(uds_path=sock, port=int(port))
        proxy.start_in_thread()
        return proxy
    except Exception:
        logger.warning("egress_proxy_start_failed", exc_info=True)
        return None


def _parse_head(head: bytes) -> "tuple[bool, str, int] | None":
    """Parse the request head, returning ``(is_connect, host, port)`` or ``None``.

    * ``CONNECT host:port HTTP/1.1`` → ``(True, host, port)``.
    * Absolute-form ``METHOD http://host[:port]/path HTTP/1.1`` → ``(False, host,
      port)`` with port defaulting to 80; the ``Host:`` header is the fallback
      source of the authority if the request line is not absolute-form.
    """
    try:
        line, _, rest = head.partition(b"\r\n")
        parts = line.split()
        if len(parts) < 2:
            return None
        method = parts[0].upper()
        target = parts[1]

        if method == b"CONNECT":
            host, port = _split_authority(target.decode("latin-1"), default_port=443)
            if not host:
                return None
            return (True, host, port)

        # Plain HTTP. Prefer the absolute-form authority on the request line.
        target_s = target.decode("latin-1")
        host = ""
        port = 80
        if "://" in target_s:
            after = target_s.split("://", 1)[1]
            authority = after.split("/", 1)[0]
            host, port = _split_authority(authority, default_port=80)
        if not host:
            # Fallback: the Host: header.
            host_hdr = _find_host_header(rest)
            if host_hdr:
                host, port = _split_authority(host_hdr, default_port=80)
        if not host:
            return None
        return (False, host, port)
    except Exception:
        return None


def _split_authority(authority: str, *, default_port: int) -> "tuple[str, int]":
    """Split ``host[:port]`` (no brackets handling beyond best-effort)."""
    authority = authority.strip()
    if ":" in authority and not authority.endswith(":"):
        host, _, port_s = authority.rpartition(":")
        try:
            return host, int(port_s)
        except ValueError:
            return authority, default_port
    return authority.rstrip(":"), default_port


def _find_host_header(rest: bytes) -> str:
    for raw in rest.split(b"\r\n"):
        if raw.lower().startswith(b"host:"):
            return raw[len(b"host:"):].strip().decode("latin-1")
    return ""


def _safe_close(writer: "asyncio.StreamWriter | None") -> None:
    if writer is None:
        return
    try:
        writer.close()
    except Exception:
        pass
