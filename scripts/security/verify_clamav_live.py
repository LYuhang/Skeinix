#!/usr/bin/env python3
"""Exercise the production UploadScanner against a real clamd Unix socket."""

from __future__ import annotations

import argparse
import asyncio

from fastapi import HTTPException

from vibecanvas_api.security.upload_scanner import (
    UploadMalwareDetected,
    UploadScannerUnavailable,
    probe_upload_scanner,
    require_clean_upload,
    scan_upload,
)


def _eicar_test_bytes() -> bytes:
    # Construct the industry-standard harmless AV test sample at runtime so a
    # workstation scanner does not quarantine this source checkout.
    return (
        b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$"
        b"EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
    )


async def _verify_live() -> None:
    await probe_upload_scanner()
    payloads = [bytes([index]) * (128 * 1024 + index) for index in range(1, 9)]
    results = await asyncio.gather(*(scan_upload(payload) for payload in payloads))
    assert all(result.clean and result.provider == "clamd" for result in results)

    try:
        await scan_upload(_eicar_test_bytes())
    except UploadMalwareDetected:
        pass
    else:
        raise AssertionError("real clamd did not detect the EICAR test sample")

    try:
        await require_clean_upload(_eicar_test_bytes())
    except HTTPException as exc:
        assert exc.status_code == 422
        assert exc.detail == "upload_malware_detected"
        assert "EICAR" not in str(exc)
    else:
        raise AssertionError("HTTP upload boundary accepted the EICAR test sample")
    print("clamav_live_gate=pass clean_concurrency=8 malware=blocked")


async def _verify_ready() -> None:
    await probe_upload_scanner()


async def _verify_unavailable() -> None:
    try:
        await scan_upload(b"ordinary file after scanner shutdown")
    except UploadScannerUnavailable:
        pass
    else:
        raise AssertionError("scanner shutdown did not fail closed")

    try:
        await require_clean_upload(b"ordinary file after scanner shutdown")
    except HTTPException as exc:
        assert exc.status_code == 503
        assert exc.detail == "upload_scanner_unavailable"
    else:
        raise AssertionError("HTTP upload boundary did not fail closed")
    print("clamav_unavailable_gate=pass status=503")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("ready", "live", "unavailable"))
    args = parser.parse_args()
    if args.mode == "ready":
        asyncio.run(_verify_ready())
    elif args.mode == "live":
        asyncio.run(_verify_live())
    else:
        asyncio.run(_verify_unavailable())


if __name__ == "__main__":
    main()
