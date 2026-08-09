#!/usr/bin/env python3
"""Measure local Object Store crypto overhead on deployment hardware."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import statistics
import tempfile
import time

from vibecanvas_api.services.object_store import FilesystemObjectStore


# Conservative deployment floors, deliberately far below the measured local
# throughput. They catch architectural regressions (for example decrypting a
# whole object for each Range request) without turning normal host variance
# into a flaky gate.
DEFAULT_MIN_WRITE_MIB_S = 75.0
DEFAULT_MIN_READ_MIB_S = 100.0
DEFAULT_MAX_RANGE_P95_MS = 10.0
DEFAULT_MIN_COLD_MATERIALIZE_MIB_S = 75.0
DEFAULT_MAX_WARM_MATERIALIZE_MS = 10.0
DEFAULT_MIN_HOT_READ_MIB_S = 250.0
DEFAULT_MAX_HOT_RANGE_P95_MS = 5.0


def _mib_per_second(size: int, seconds: float) -> float:
    return size / (1024 * 1024) / max(seconds, 1e-9)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size-mib", type=int, default=64)
    parser.add_argument("--range-reads", type=int, default=200)
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="print measurements without enforcing the deployment budgets",
    )
    args = parser.parse_args()
    size = max(1, args.size_mib) * 1024 * 1024
    pattern = bytes(range(256)) * 4096
    payload = (pattern * (size // len(pattern) + 1))[:size]
    with tempfile.TemporaryDirectory(prefix="vc-object-bench-") as temporary:
        root = Path(temporary) / "cipher"
        store = FilesystemObjectStore(
            root=str(root),
            materialized_root=str(Path(temporary) / "materialized"),
            master_key=os.urandom(32),
        )
        object_path = "artifacts/benchmark/payload.bin"

        started = time.perf_counter()
        store.put_bytes(object_path, payload)
        write_seconds = time.perf_counter() - started

        started = time.perf_counter()
        restored = b"".join(
            store.iter_bytes(object_path, chunk_size=1024 * 1024)
        )
        read_seconds = time.perf_counter() - started
        if restored != payload:
            raise RuntimeError("sequential crypto roundtrip mismatch")

        latencies: list[float] = []
        reads = max(1, args.range_reads)
        for index in range(reads):
            start = (index * 104729) % max(1, size - 64 * 1024)
            tick = time.perf_counter()
            value = b"".join(store.iter_bytes(
                object_path,
                start=start,
                end=start + 64 * 1024 - 1,
                chunk_size=64 * 1024,
            ))
            latencies.append((time.perf_counter() - tick) * 1000)
            if value != payload[start:start + 64 * 1024]:
                raise RuntimeError("range crypto roundtrip mismatch")

        run_object_path = "run/benchmark/session/workspace.bin"
        store.put_bytes(run_object_path, payload)
        tick = time.perf_counter()
        materialized = store.materialize_prefix("run/benchmark/session/")
        hydrate_seconds = time.perf_counter() - tick
        tick = time.perf_counter()
        repeated = store.materialize_prefix("run/benchmark/session/")
        warm_materialize_ms = (time.perf_counter() - tick) * 1000
        if materialized != repeated:
            raise RuntimeError("materialization cache is unstable")

        # Once a sandbox is resident, Preview and host-side file reads must use
        # the process-private 0600 mirror. Re-decrypting the durable VCOBJ2
        # object here would make high-frequency interaction noticeably slower.
        tick = time.perf_counter()
        hot_restored = b"".join(
            store.iter_bytes(run_object_path, chunk_size=1024 * 1024)
        )
        hot_read_seconds = time.perf_counter() - tick
        if hot_restored != payload:
            raise RuntimeError("active materialization roundtrip mismatch")

        hot_latencies: list[float] = []
        for index in range(reads):
            start = (index * 104729) % max(1, size - 64 * 1024)
            tick = time.perf_counter()
            value = b"".join(store.iter_bytes(
                run_object_path,
                start=start,
                end=start + 64 * 1024 - 1,
                chunk_size=64 * 1024,
            ))
            hot_latencies.append((time.perf_counter() - tick) * 1000)
            if value != payload[start:start + 64 * 1024]:
                raise RuntimeError("active materialization range mismatch")

    ordered = sorted(latencies)
    p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
    hot_ordered = sorted(hot_latencies)
    hot_p95 = hot_ordered[
        min(len(hot_ordered) - 1, int(len(hot_ordered) * 0.95))
    ]
    write_mib_s = _mib_per_second(size, write_seconds)
    read_mib_s = _mib_per_second(size, read_seconds)
    cold_materialize_mib_s = _mib_per_second(size, hydrate_seconds)
    hot_read_mib_s = _mib_per_second(size, hot_read_seconds)
    print(f"payload_mib={size / (1024 * 1024):.1f}")
    print(f"encrypted_write_mib_s={write_mib_s:.1f}")
    print(f"decrypted_read_mib_s={read_mib_s:.1f}")
    print(f"range_64k_p50_ms={statistics.median(latencies):.3f}")
    print(f"range_64k_p95_ms={p95:.3f}")
    print(f"cold_materialize_mib_s={cold_materialize_mib_s:.1f}")
    print(f"warm_materialize_ms={warm_materialize_ms:.3f}")
    print(f"active_materialized_read_mib_s={hot_read_mib_s:.1f}")
    print(f"active_materialized_range_64k_p95_ms={hot_p95:.3f}")
    if args.report_only:
        return 0

    failures: list[str] = []
    if write_mib_s < DEFAULT_MIN_WRITE_MIB_S:
        failures.append(
            f"encrypted write {write_mib_s:.1f} MiB/s is below "
            f"{DEFAULT_MIN_WRITE_MIB_S:.1f} MiB/s"
        )
    if read_mib_s < DEFAULT_MIN_READ_MIB_S:
        failures.append(
            f"sequential decrypt {read_mib_s:.1f} MiB/s is below "
            f"{DEFAULT_MIN_READ_MIB_S:.1f} MiB/s"
        )
    if p95 > DEFAULT_MAX_RANGE_P95_MS:
        failures.append(
            f"64 KiB Range p95 {p95:.3f} ms exceeds "
            f"{DEFAULT_MAX_RANGE_P95_MS:.3f} ms"
        )
    if cold_materialize_mib_s < DEFAULT_MIN_COLD_MATERIALIZE_MIB_S:
        failures.append(
            f"cold materialization {cold_materialize_mib_s:.1f} MiB/s is below "
            f"{DEFAULT_MIN_COLD_MATERIALIZE_MIB_S:.1f} MiB/s"
        )
    if warm_materialize_ms > DEFAULT_MAX_WARM_MATERIALIZE_MS:
        failures.append(
            f"warm materialization {warm_materialize_ms:.3f} ms exceeds "
            f"{DEFAULT_MAX_WARM_MATERIALIZE_MS:.3f} ms"
        )
    if hot_read_mib_s < DEFAULT_MIN_HOT_READ_MIB_S:
        failures.append(
            f"active materialized read {hot_read_mib_s:.1f} MiB/s is below "
            f"{DEFAULT_MIN_HOT_READ_MIB_S:.1f} MiB/s"
        )
    if hot_p95 > DEFAULT_MAX_HOT_RANGE_P95_MS:
        failures.append(
            f"active materialized 64 KiB Range p95 {hot_p95:.3f} ms exceeds "
            f"{DEFAULT_MAX_HOT_RANGE_P95_MS:.3f} ms"
        )
    if failures:
        for failure in failures:
            print(f"budget_failure={failure}")
        return 1
    print("performance_budget=pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
