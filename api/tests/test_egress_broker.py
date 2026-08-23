# -*- coding: utf-8 -*-
"""Tests for the host-side egress broker (sandbox-egress B-task).

A real local asyncio echo server stands in for the "real target"; a raw
asyncio UDS client stands in for the in-sandbox forward proxy. We drive the
per-connection wire protocol (4-byte BE length prefix + JSON header
``{"host","port"}`` → 1 status byte → bidirectional relay) directly.

``asyncio_mode = "auto"`` (api/pyproject.toml) → async tests need no marker.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import struct

from vibecanvas_api.services.sandbox.egress_broker import (
    EgressBroker,
    _open_validated_connection,
    _resolve_public_addresses,
)

_LEN = struct.Struct(">I")


def _encode_header(host: str, port: int) -> bytes:
    body = json.dumps({"host": host, "port": port}).encode("utf-8")
    return _LEN.pack(len(body)) + body


async def _start_echo_server():
    """A 127.0.0.1:0 TCP echo server standing in for the real target.

    Returns ``(server, host, port, got_connection_event)``.
    """
    got_connection = asyncio.Event()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        got_connection.set()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    host, port = server.sockets[0].getsockname()[:2]
    return server, host, port, got_connection


async def _start_broker(socket_path: str, allow_hosts: set[str]) -> EgressBroker:
    broker = EgressBroker(socket_path, allow_hosts=allow_hosts, run_id="run-test-1234")
    await broker.start()
    return broker


async def test_allowed_host_relays_bytes(tmp_path, monkeypatch):
    server, host, port, _got = await _start_echo_server()
    async def validated_addresses(_host: str, _port: int) -> tuple[str, ...]:
        return (host,)

    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.egress_broker._resolve_public_addresses",
        validated_addresses,
    )
    sock_path = str(tmp_path / "egress.sock")
    broker = await _start_broker(sock_path, allow_hosts={host})
    try:
        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        writer.write(_encode_header(host, port))
        await writer.drain()

        status = await reader.readexactly(1)
        assert status == b"\x01"

        writer.write(b"ping")
        await writer.drain()
        echoed = await reader.readexactly(4)
        assert echoed == b"ping"

        writer.close()
    finally:
        await broker.aclose()
        server.close()
        await server.wait_closed()


async def test_blocked_host_denied(tmp_path):
    server, host, port, got_connection = await _start_echo_server()
    sock_path = str(tmp_path / "egress.sock")
    broker = await _start_broker(sock_path, allow_hosts={"allowed.test"})
    try:
        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        writer.write(_encode_header("evil.test", 443))
        await writer.drain()

        status = await reader.readexactly(1)
        assert status == b"\x00"

        # Connection should close after the deny byte → EOF on next read.
        rest = await reader.read()
        assert rest == b""
        writer.close()

        # No dial happened against the real target.
        await asyncio.sleep(0.05)
        assert not got_connection.is_set()
    finally:
        await broker.aclose()
        server.close()
        await server.wait_closed()


async def test_allowlisted_private_destination_is_still_denied(tmp_path):
    """An allowlist hostname is not authority to reach loopback/private IPs."""
    server, host, port, got_connection = await _start_echo_server()
    sock_path = str(tmp_path / "egress.sock")
    broker = await _start_broker(sock_path, allow_hosts={host})
    try:
        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        writer.write(_encode_header(host, port))
        await writer.drain()

        assert await reader.readexactly(1) == b"\x00"
        assert await reader.read() == b""
        await asyncio.sleep(0.05)
        assert not got_connection.is_set()
        writer.close()
    finally:
        await broker.aclose()
        server.close()
        await server.wait_closed()


async def test_public_policy_allows_unlisted_public_host(tmp_path, monkeypatch):
    server, host, port, _got = await _start_echo_server()

    async def validated_addresses(_host: str, _port: int) -> tuple[str, ...]:
        return (host,)

    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.egress_broker._resolve_public_addresses",
        validated_addresses,
    )
    sock_path = str(tmp_path / "egress.sock")
    broker = EgressBroker(
        sock_path,
        allow_hosts=set(),
        allow_public=True,
        run_id="run-public",
    )
    await broker.start()
    try:
        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        writer.write(_encode_header("user-selected.example", port))
        await writer.drain()
        assert await reader.readexactly(1) == b"\x01"
        writer.write(b"web")
        await writer.drain()
        assert await reader.readexactly(3) == b"web"
        writer.close()
    finally:
        await broker.aclose()
        server.close()
        await server.wait_closed()


async def test_exact_private_target_is_allowed_without_opening_private_ranges(tmp_path):
    server, host, port, _got = await _start_echo_server()
    sock_path = str(tmp_path / "egress.sock")
    broker = EgressBroker(
        sock_path,
        allow_hosts=set(),
        allow_private_targets={(host, port)},
        run_id="run-private-exact",
    )
    await broker.start()
    try:
        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        writer.write(_encode_header(host, port))
        await writer.drain()
        assert await reader.readexactly(1) == b"\x01"
        writer.close()

        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        writer.write(_encode_header(host, port + 1))
        await writer.drain()
        assert await reader.readexactly(1) == b"\x00"
        writer.close()
    finally:
        await broker.aclose()
        server.close()
        await server.wait_closed()


async def test_trusted_fake_ip_dns_requires_a_hostname(monkeypatch):
    fake_network = ipaddress.ip_network("198.18.0.0/15")

    def fake_getaddrinfo(host, port, **_kwargs):
        return [(2, 1, 6, "", ("198.18.0.7", port))]

    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.egress_broker.socket.getaddrinfo",
        fake_getaddrinfo,
    )

    assert await _resolve_public_addresses("example.com", 443) == ()
    assert await _resolve_public_addresses(
        "example.com", 443, (fake_network,)
    ) == ("198.18.0.7",)
    # A literal synthetic/private address is never upgraded to public access.
    assert await _resolve_public_addresses(
        "198.18.0.7", 443, (fake_network,)
    ) == ()


async def test_validated_connection_retries_a_transient_dial_failure(monkeypatch):
    attempts: list[tuple[str, int]] = []
    expected = (object(), object())

    async def connect(host: str, port: int):
        attempts.append((host, port))
        if len(attempts) < 3:
            raise OSError("transient proxy dial failure")
        return expected

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "open_connection", connect)
    monkeypatch.setattr(asyncio, "sleep", no_delay)

    assert await _open_validated_connection(("198.18.0.36",), 443) == expected
    assert attempts == [("198.18.0.36", 443)] * 3


async def test_suffix_match(tmp_path):
    sock_path = str(tmp_path / "egress.sock")
    broker = EgressBroker(
        sock_path, allow_hosts={".example.com"}, run_id="run-suffix"
    )
    assert broker._host_allowed("api.example.com") is True
    assert broker._host_allowed("example.com") is True
    assert broker._host_allowed("evil.com") is False
    # Case-insensitive.
    assert broker._host_allowed("API.Example.COM") is True

    # Empty allowlist → deny all (fail-closed).
    deny_all = EgressBroker(sock_path, allow_hosts=set(), run_id="run-empty")
    assert deny_all._host_allowed("anything.test") is False

    # Exact (non-dot) match, case-insensitive.
    exact = EgressBroker(sock_path, allow_hosts={"Host.Test"}, run_id="run-exact")
    assert exact._host_allowed("host.test") is True
    assert exact._host_allowed("sub.host.test") is False


def test_operation_host_lease_is_revoked_without_affecting_overlapping_jobs(tmp_path):
    broker = EgressBroker(
        str(tmp_path / "egress.sock"),
        allow_hosts={"baseline.example"},
        run_id="resident-scoped",
    )

    first = broker.acquire_allow_hosts({"first.example", "shared.example"})
    second = broker.acquire_allow_hosts({"second.example", "shared.example"})
    assert broker._host_allowed("first.example") is True
    assert broker._host_allowed("second.example") is True
    assert broker._host_allowed("shared.example") is True

    broker.release_allow_hosts(first)
    assert broker._host_allowed("first.example") is False
    assert broker._host_allowed("shared.example") is True
    broker.release_allow_hosts(second)
    assert broker._host_allowed("second.example") is False
    assert broker._host_allowed("shared.example") is False
    assert broker._host_allowed("baseline.example") is True

    # Teardown paths may race/double-release without restoring authority.
    broker.release_allow_hosts(first)


async def test_oversized_header_rejected_without_allocation(tmp_path):
    """A header frame whose declared length exceeds the broker's header cap is
    rejected WITHOUT the broker allocating/reading that many body bytes (memory
    DoS hardening). We send only the oversized 4-byte prefix (no body): the
    broker must drop the connection (EOF, NO status byte) rather than block
    waiting to allocate the huge body."""
    from vibecanvas_api.services.sandbox.egress_broker import _MAX_HEADER_LEN

    server, host, port, got_connection = await _start_echo_server()
    sock_path = str(tmp_path / "egress.sock")
    broker = await _start_broker(sock_path, allow_hosts={host})
    try:
        reader, writer = await asyncio.open_unix_connection(path=sock_path)
        # Declare a body far larger than the cap, then send NO body bytes.
        writer.write(_LEN.pack(_MAX_HEADER_LEN + 1))
        await writer.drain()

        # The broker rejects on the cap (ValueError path) and closes — we get a
        # clean EOF, NOT a status byte and NOT a hang on a huge allocation.
        rest = await asyncio.wait_for(reader.read(), timeout=2.0)
        assert rest == b""  # connection closed, no status byte emitted.
        writer.close()

        # No dial happened against the real target.
        await asyncio.sleep(0.05)
        assert not got_connection.is_set()
    finally:
        await broker.aclose()
        server.close()
        await server.wait_closed()


async def test_aclose_cleans_up(tmp_path):
    sock_path = str(tmp_path / "sub" / "egress.sock")
    broker = EgressBroker(sock_path, allow_hosts={"x"}, run_id="run-clean")
    await broker.start()
    assert os.path.exists(sock_path)
    await broker.aclose()
    assert not os.path.exists(sock_path)
    # Idempotent.
    await broker.aclose()
