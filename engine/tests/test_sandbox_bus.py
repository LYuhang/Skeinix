# -*- coding: utf-8 -*-
"""Engine bus wire-protocol and connector tests without gVisor.

Covers: framing round-trips (4-byte length prefix + JSON, ``default=str`` on a
non-serializable), the connector's retry-connect loop, and a host-listener ↔
connector loopback (the same UDS bus the api host broker runs, exercised here
with a hand-rolled ``asyncio.start_unix_server`` listener — no api import)."""

from __future__ import annotations

import asyncio
import os
import struct
import tempfile
from decimal import Decimal

import pytest

from vibecanvas_engine.sandbox_bus import (
    MSG_NODE_EVENT,
    MSG_RESULT,
    UdsClientChannel,
    connect_bus,
    encode_frame,
    read_frame,
)


def test_encode_frame_length_prefix():
    """One frame = a 4-byte big-endian length prefix + the JSON body bytes."""
    frame = encode_frame({"type": MSG_NODE_EVENT, "node_id": "node_1"})
    (length,) = struct.unpack(">I", frame[:4])
    body = frame[4:]
    assert length == len(body)
    assert b'"node_1"' in body


@pytest.mark.asyncio
async def test_frame_round_trip_via_pipe():
    """encode_frame → read_frame over a real StreamReader round-trips the dict."""
    msg = {"type": MSG_NODE_EVENT, "status": "running", "node_id": "n1"}
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(msg))
    reader.feed_eof()
    got = await read_frame(reader)
    assert got == msg
    # Clean EOF at a frame boundary → None (peer closed).
    assert await read_frame(reader) is None


@pytest.mark.asyncio
async def test_read_frame_max_len_rejects_oversized_prefix():
    """With ``max_len`` set, an oversized length prefix raises ValueError BEFORE
    the body ``readexactly`` allocation (memory-DoS hardening). We only feed the
    4-byte prefix (no body) — if read_frame tried to allocate/read the body it
    would block on the missing bytes instead of raising the cap error."""
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack(">I", 10_000_000))  # claims ~10MB body
    # Do NOT feed the body — the cap must fire before any readexactly(length).
    with pytest.raises(ValueError):
        await read_frame(reader, max_len=65536)


@pytest.mark.asyncio
async def test_read_frame_under_max_len_round_trips():
    """A frame at/under ``max_len`` round-trips exactly as without a cap."""
    msg = {"type": MSG_NODE_EVENT, "node_id": "n1"}
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(msg))
    reader.feed_eof()
    got = await read_frame(reader, max_len=65536)
    assert got == msg


@pytest.mark.asyncio
async def test_read_frame_default_uncapped_bus_unchanged():
    """Default (no ``max_len``) is UNCAPPED — a large body that would exceed a
    cap still reads successfully, proving bus behavior is unchanged."""
    big = {"type": MSG_RESULT, "blob": "x" * 200_000}  # > 64KB body
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(big))
    reader.feed_eof()
    got = await read_frame(reader)  # no max_len → uncapped
    assert got == big


@pytest.mark.asyncio
async def test_encode_frame_default_str_non_serializable():
    """A non-JSON-native value (Decimal / set) degrades via default=str instead of
    raising mid-stream."""
    msg = {"type": MSG_RESULT, "final_outputs": {"x": Decimal("1.5"), "s": {1, 2}}}
    reader = asyncio.StreamReader()
    reader.feed_data(encode_frame(msg))
    reader.feed_eof()
    got = await read_frame(reader)
    assert got["final_outputs"]["x"] == "1.5"  # Decimal → str
    assert isinstance(got["final_outputs"]["s"], str)  # set → str


@pytest.mark.asyncio
async def test_connect_bus_retries_until_listener_up():
    """The connector tolerates ECONNREFUSED/ENOENT and retries until the host
    listener is bound (startup-ordering — the sandbox may connect before bind)."""
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "bus.sock")
        connected: list[asyncio.StreamReader] = []

        async def _on_conn(reader, writer):
            connected.append(reader)

        # Start the connector FIRST (socket does not exist yet) → it must retry.
        connect_task = asyncio.create_task(
            connect_bus(sock, retries=200, delay=0.01)
        )
        await asyncio.sleep(0.05)  # let it spin on ENOENT a few times
        assert not connect_task.done()

        server = await asyncio.start_unix_server(_on_conn, path=sock)
        chan = await asyncio.wait_for(connect_task, timeout=2.0)
        assert isinstance(chan, UdsClientChannel)
        await chan.close()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_loopback_full_duplex():
    """Host-listener ↔ connector loopback: the connector sends node_event/result
    frames; the host reads them framed; the host can send back (full-duplex)."""
    with tempfile.TemporaryDirectory() as d:
        sock = os.path.join(d, "bus.sock")
        host_reader_holder: dict = {}

        async def _on_conn(reader, writer):
            host_reader_holder["reader"] = reader
            host_reader_holder["writer"] = writer

        server = await asyncio.start_unix_server(_on_conn, path=sock)
        chan = await connect_bus(sock, retries=50, delay=0.01)
        # Wait for the accept callback to populate the host side.
        for _ in range(100):
            if "reader" in host_reader_holder:
                break
            await asyncio.sleep(0.01)
        host_reader = host_reader_holder["reader"]

        await chan.send({"type": MSG_NODE_EVENT, "node_id": "n1", "status": "running"})
        await chan.send({"type": MSG_RESULT, "final_outputs": {"ok": True},
                         "error_dict": {}, "execution_time": 0.1})
        m1 = await read_frame(host_reader)
        m2 = await read_frame(host_reader)
        assert m1 == {"type": MSG_NODE_EVENT, "node_id": "n1", "status": "running"}
        assert m2["type"] == MSG_RESULT and m2["final_outputs"] == {"ok": True}

        # Full-duplex: host → sandbox (the reserved inject direction).
        host_writer = host_reader_holder["writer"]
        host_writer.write(encode_frame({"type": "inject", "payload": "frame-1"}))
        await host_writer.drain()
        injected = await chan.recv()
        assert injected == {"type": "inject", "payload": "frame-1"}

        await chan.close()
        server.close()
        await server.wait_closed()
