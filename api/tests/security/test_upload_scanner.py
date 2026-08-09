from __future__ import annotations

import asyncio
import struct

from fastapi import HTTPException
import pytest

from vibecanvas_api.config import config
from vibecanvas_api.security.upload_scanner import (
    UploadMalwareDetected,
    UploadScannerUnavailable,
    probe_upload_scanner,
    require_clean_upload,
    scan_upload,
)


async def _serve_clamd_once(socket_path, verdict: bytes, received: bytearray):
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        try:
            assert await reader.readuntil(b"\x00") == b"zINSTREAM\x00"
            while True:
                size = struct.unpack("!I", await reader.readexactly(4))[0]
                if size == 0:
                    break
                received.extend(await reader.readexactly(size))
            writer.write(verdict + b"\x00")
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    return await asyncio.start_unix_server(handler, path=socket_path)


@pytest.mark.asyncio
async def test_disabled_scanner_preserves_development_uploads(monkeypatch):
    monkeypatch.setattr(config, "upload_scanner_provider", "disabled")
    result = await scan_upload(b"ordinary user file")
    assert result.clean is True
    assert result.provider == "disabled"


@pytest.mark.asyncio
async def test_clamd_scans_complete_bytes_before_clean_verdict(tmp_path, monkeypatch):
    socket_path = tmp_path / "clamd.sock"
    received = bytearray()
    server = await _serve_clamd_once(socket_path, b"stream: OK", received)
    monkeypatch.setattr(config, "upload_scanner_provider", "clamd")
    monkeypatch.setattr(
        config,
        "upload_scanner_clamd_unix_socket",
        str(socket_path),
    )
    monkeypatch.setattr(config, "upload_scanner_timeout_seconds", 2.0)
    payload = b"a" * (64 * 1024 + 17)
    try:
        result = await scan_upload(payload)
    finally:
        server.close()
        await server.wait_closed()
    assert result.clean is True
    assert result.provider == "clamd"
    assert received == payload


@pytest.mark.asyncio
async def test_clamd_readiness_uses_ping_without_file_content(tmp_path, monkeypatch):
    socket_path = tmp_path / "clamd-ping.sock"

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        assert await reader.readuntil(b"\x00") == b"zPING\x00"
        writer.write(b"PONG\x00")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    server = await asyncio.start_unix_server(handler, path=socket_path)
    monkeypatch.setattr(config, "upload_scanner_provider", "clamd")
    monkeypatch.setattr(
        config,
        "upload_scanner_clamd_unix_socket",
        str(socket_path),
    )
    monkeypatch.setattr(config, "upload_scanner_timeout_seconds", 2.0)
    try:
        await probe_upload_scanner()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_clamd_malware_verdict_is_stable_and_content_free(tmp_path, monkeypatch):
    socket_path = tmp_path / "clamd-first.sock"
    server = await _serve_clamd_once(
        socket_path,
        b"stream: Eicar-Signature FOUND",
        bytearray(),
    )
    monkeypatch.setattr(config, "upload_scanner_provider", "clamd")
    monkeypatch.setattr(
        config,
        "upload_scanner_clamd_unix_socket",
        str(socket_path),
    )
    monkeypatch.setattr(config, "upload_scanner_timeout_seconds", 2.0)
    try:
        with pytest.raises(UploadMalwareDetected):
            await scan_upload(b"sensitive sample bytes")
    finally:
        server.close()
        await server.wait_closed()

    socket_path = tmp_path / "clamd-second.sock"
    monkeypatch.setattr(
        config,
        "upload_scanner_clamd_unix_socket",
        str(socket_path),
    )
    server = await _serve_clamd_once(
        socket_path,
        b"stream: Eicar-Signature FOUND",
        bytearray(),
    )
    try:
        with pytest.raises(HTTPException) as exc_info:
            await require_clean_upload(b"sensitive sample bytes")
    finally:
        server.close()
        await server.wait_closed()
    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "upload_malware_detected"
    assert "sensitive" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_clamd_unavailable_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "upload_scanner_provider", "clamd")
    monkeypatch.setattr(
        config,
        "upload_scanner_clamd_unix_socket",
        str(tmp_path / "missing.sock"),
    )
    monkeypatch.setattr(config, "upload_scanner_timeout_seconds", 0.2)
    with pytest.raises(UploadScannerUnavailable):
        await scan_upload(b"file")
    with pytest.raises(HTTPException) as exc_info:
        await require_clean_upload(b"file")
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "upload_scanner_unavailable"
