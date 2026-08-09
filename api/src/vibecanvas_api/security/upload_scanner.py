"""Fail-closed malware scanning for user-controlled file ingress.

The application never writes an unscanned production upload to PostgreSQL,
Object Store, VFS, KB parser queues, Preview, or Runtime mounts.  Development
and tests keep an explicit disabled provider so the source checkout does not
depend on a local daemon.  Production validation requires the ``clamd``
provider over a Unix socket.

Only opaque bytes and the clamd wire protocol cross this boundary.  Filenames,
tenant identifiers, paths, and user content are not logged or sent as command
arguments.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import struct

from fastapi import HTTPException, status

from vibecanvas_api.config import config


_CHUNK_SIZE = 64 * 1024
_MAX_RESPONSE_BYTES = 4096


class UploadScanError(RuntimeError):
    """Base error for the upload-scanning boundary."""


class UploadMalwareDetected(UploadScanError):
    """The configured scanner identified malicious content."""


class UploadScannerUnavailable(UploadScanError):
    """The configured scanner could not produce an authoritative result."""


@dataclass(frozen=True, slots=True)
class UploadScanResult:
    provider: str
    clean: bool


async def _read_clamd_response(reader: asyncio.StreamReader) -> bytes:
    try:
        response = await reader.readuntil(b"\x00")
    except asyncio.IncompleteReadError as exc:
        response = exc.partial
    except asyncio.LimitOverrunError as exc:
        raise UploadScannerUnavailable("upload scanner response exceeded limit") from exc
    if not response or len(response) > _MAX_RESPONSE_BYTES:
        raise UploadScannerUnavailable("upload scanner returned an invalid response")
    return response.rstrip(b"\x00\r\n")


async def _scan_with_clamd(data: bytes) -> UploadScanResult:
    socket_path = config.upload_scanner_clamd_unix_socket
    if not socket_path:
        raise UploadScannerUnavailable("upload scanner socket is not configured")

    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(config.upload_scanner_timeout_seconds):
            reader, writer = await asyncio.open_unix_connection(
                socket_path,
                limit=_MAX_RESPONSE_BYTES,
            )
            writer.write(b"zINSTREAM\x00")
            for offset in range(0, len(data), _CHUNK_SIZE):
                chunk = data[offset : offset + _CHUNK_SIZE]
                writer.write(struct.pack("!I", len(chunk)))
                writer.write(chunk)
                await writer.drain()
            writer.write(struct.pack("!I", 0))
            await writer.drain()
            response = await _read_clamd_response(reader)
    except UploadScanError:
        raise
    except (TimeoutError, OSError, ConnectionError) as exc:
        raise UploadScannerUnavailable("upload scanner is unavailable") from exc
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    if response.endswith(b": OK"):
        return UploadScanResult(provider="clamd", clean=True)
    if response.endswith(b" FOUND"):
        raise UploadMalwareDetected("malware detected in upload")
    raise UploadScannerUnavailable("upload scanner did not return a clean verdict")


async def probe_upload_scanner() -> None:
    """Verify configured scanner reachability before the API becomes ready."""
    provider = config.upload_scanner_provider
    if provider == "disabled":
        return
    if provider != "clamd":
        raise UploadScannerUnavailable("upload scanner provider is unsupported")
    socket_path = config.upload_scanner_clamd_unix_socket
    if not socket_path:
        raise UploadScannerUnavailable("upload scanner socket is not configured")

    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(config.upload_scanner_timeout_seconds):
            reader, writer = await asyncio.open_unix_connection(
                socket_path,
                limit=_MAX_RESPONSE_BYTES,
            )
            writer.write(b"zPING\x00")
            await writer.drain()
            response = await _read_clamd_response(reader)
    except UploadScanError:
        raise
    except (TimeoutError, OSError, ConnectionError) as exc:
        raise UploadScannerUnavailable("upload scanner is unavailable") from exc
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    if response != b"PONG":
        raise UploadScannerUnavailable("upload scanner readiness probe failed")


async def scan_upload(data: bytes) -> UploadScanResult:
    """Return a clean verdict or raise without exposing content in the error."""
    provider = config.upload_scanner_provider
    if provider == "disabled":
        return UploadScanResult(provider="disabled", clean=True)
    if provider == "clamd":
        return await _scan_with_clamd(data)
    # AppConfig rejects unknown providers; retain a fail-closed seam for tests
    # and future runtime mutation.
    raise UploadScannerUnavailable("upload scanner provider is unsupported")


async def require_clean_upload(data: bytes) -> UploadScanResult:
    """FastAPI boundary that maps scanner outcomes to stable, safe errors."""
    try:
        return await scan_upload(data)
    except UploadMalwareDetected as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="upload_malware_detected",
        ) from exc
    except UploadScannerUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="upload_scanner_unavailable",
        ) from exc
