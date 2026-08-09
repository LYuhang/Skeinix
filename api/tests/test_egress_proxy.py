# -*- coding: utf-8 -*-
"""Tests for the in-sandbox egress forward proxy (engine-side).

A FAKE host broker (a tiny asyncio unix server) stands in for the real
``EgressBroker``: it reads the header frame, replies with a status byte, then
echoes every subsequent byte so a tunneled stream round-trips. The real
``EgressProxy`` is started against that fake UDS on a loopback TCP port and we
drive it through real sockets.
"""

from __future__ import annotations

import asyncio
import os
import socket
import tempfile


from vibecanvas_engine.egress_proxy import EgressProxy, maybe_start_egress_proxy
from vibecanvas_engine.sandbox_bus import read_frame


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeBroker:
    """Tiny asyncio unix server: read header frame, send a status byte, echo."""

    def __init__(self, socket_path: str, *, status: bytes = b"\x01"):
        self.socket_path = socket_path
        self.status = status
        self.headers: list[dict] = []
        self.received = bytearray()
        self._server = None

    async def start(self) -> None:
        self._server = await asyncio.start_unix_server(
            self._handle, path=self.socket_path
        )

    async def aclose(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await self._server.wait_closed()
            except Exception:
                pass

    async def _handle(self, reader, writer):
        try:
            header = await read_frame(reader)
            if isinstance(header, dict):
                self.headers.append(header)
            writer.write(self.status)
            await writer.drain()
            if self.status != b"\x01":
                writer.close()
                return
            # echo loop
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                self.received.extend(data)
                writer.write(data)
                await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass


def _uds_path() -> str:
    d = tempfile.mkdtemp(prefix="egress-test-")
    return os.path.join(d, "egress.sock")


async def test_connect_tunnel_established_and_relays():
    uds = _uds_path()
    broker = _FakeBroker(uds, status=b"\x01")
    await broker.start()
    port = _free_tcp_port()
    proxy = EgressProxy(uds, port=port)
    proxy.start_in_thread()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT echo.test:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        # read the full status block (status line + terminating CRLFCRLF)
        status_block = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"), timeout=5
        )
        assert status_block.startswith(b"HTTP/1.1 200"), status_block
        # now tunnel: send bytes, expect echo
        writer.write(b"hello")
        await writer.drain()
        echoed = await asyncio.wait_for(reader.readexactly(5), timeout=5)
        assert echoed == b"hello"
        writer.close()
    finally:
        await broker.aclose()
    assert {"host": "echo.test", "port": 443} in broker.headers


async def test_deny_returns_403():
    uds = _uds_path()
    broker = _FakeBroker(uds, status=b"\x00")
    await broker.start()
    port = _free_tcp_port()
    proxy = EgressProxy(uds, port=port)
    proxy.start_in_thread()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"CONNECT blocked.test:443 HTTP/1.1\r\n\r\n")
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=5)
        assert line.startswith(b"HTTP/1.1 403"), line
        writer.close()
    finally:
        await broker.aclose()


async def test_plain_http_forwards_request():
    uds = _uds_path()
    broker = _FakeBroker(uds, status=b"\x01")
    await broker.start()
    port = _free_tcp_port()
    proxy = EgressProxy(uds, port=port)
    proxy.start_in_thread()
    request = b"GET http://echo.test/path HTTP/1.1\r\nHost: echo.test\r\n\r\n"
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(request)
        await writer.drain()
        # broker echoes the forwarded request head back
        echoed = await asyncio.wait_for(reader.readexactly(len(request)), timeout=5)
        assert echoed == request
        writer.close()
    finally:
        await broker.aclose()
    assert {"host": "echo.test", "port": 80} in broker.headers
    assert bytes(broker.received).startswith(b"GET http://echo.test/path")


async def test_start_in_thread_listens():
    uds = _uds_path()
    broker = _FakeBroker(uds, status=b"\x01")
    await broker.start()
    port = _free_tcp_port()
    proxy = EgressProxy(uds, port=port)
    proxy.start_in_thread()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
    finally:
        await broker.aclose()


# ---- maybe_start_egress_proxy (B4 entrypoint startup hook) -----------------


def test_maybe_start_egress_proxy_noop_when_env_unset(monkeypatch):
    """Dev / host-network mode: neither env var set → starts nothing, returns None."""
    monkeypatch.delenv("VC_EGRESS_SOCK", raising=False)
    monkeypatch.delenv("VC_EGRESS_PORT", raising=False)
    assert maybe_start_egress_proxy() is None


async def test_maybe_start_egress_proxy_starts_when_env_set(monkeypatch):
    """Proxy mode: both env vars set → returns an EgressProxy that is listening."""
    uds = _uds_path()
    broker = _FakeBroker(uds, status=b"\x01")
    await broker.start()
    port = _free_tcp_port()
    monkeypatch.setenv("VC_EGRESS_SOCK", uds)
    monkeypatch.setenv("VC_EGRESS_PORT", str(port))
    proxy = maybe_start_egress_proxy()
    try:
        assert isinstance(proxy, EgressProxy)
        # TCP connect succeeds → the proxy is actually listening on the port.
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.close()
    finally:
        await broker.aclose()


def test_maybe_start_egress_proxy_failsoft(monkeypatch):
    """Malformed port (int() raises) → fail-soft: returns None, never raises."""
    monkeypatch.setenv("VC_EGRESS_SOCK", _uds_path())
    monkeypatch.setenv("VC_EGRESS_PORT", "not-an-int")
    assert maybe_start_egress_proxy() is None
