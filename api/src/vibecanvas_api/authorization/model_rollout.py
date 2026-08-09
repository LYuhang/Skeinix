"""Immutable OpenFGA model publish, canary, promote, and rollback control plane."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Iterator

from . import bootstrap


_MODEL_DIR = Path(__file__).with_name("model")
_DEFAULT_MODEL = _MODEL_DIR / "model.json"
_DEFAULT_CONFIG = Path("/run/vibecanvas-openfga/config.json")
_SOURCE_SHA = re.compile(r"[0-9a-f]{40}")
_SOURCE_REF = re.compile(r"refs/tags/v\S+")
_CONTROL_PLANE_ID = re.compile(r"[0-9A-Za-z_-]+")
_MODEL_DIGEST = re.compile(r"[0-9a-f]{64}")
_MAX_SAMPLE_BYTES = 8 * 1024 * 1024
_MAX_SAMPLE_ROWS = 5_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _control_plane() -> tuple[str, str, str]:
    api_url = _required_env("OPENFGA_API_URL").rstrip("/")
    api_token = _required_env("OPENFGA_API_TOKEN")
    store_id = _required_env("OPENFGA_STORE_ID")
    if not _CONTROL_PLANE_ID.fullmatch(store_id):
        raise RuntimeError("OPENFGA_STORE_ID is invalid")
    return api_url, api_token, store_id


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"invalid or missing rollout artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"rollout artifact must be a JSON object: {path}")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


@contextmanager
def _config_lock(config_path: Path) -> Iterator[None]:
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = config_path.with_name(f".{config_path.name}.lock")
    with lock_path.open("a", encoding="utf-8") as handle:
        os.chmod(lock_path, 0o600)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield


def _load_model(path: Path) -> tuple[dict[str, Any], str]:
    payload = _read_json(path)
    return payload, bootstrap.model_sha256(payload)


def publish(*, model_path: Path, candidate_path: Path) -> dict[str, Any]:
    api_url, api_token, store_id = _control_plane()
    model, digest = _load_model(model_path)
    if candidate_path.exists():
        existing = _read_json(candidate_path)
        if (
            existing.get("store_id") == store_id
            and existing.get("model_sha256") == digest
            and isinstance(existing.get("authorization_model_id"), str)
        ):
            bootstrap.validate_remote_model(
                api_url,
                api_token,
                store_id=store_id,
                model_id=existing["authorization_model_id"],
                expected_model=model,
                expected_digest=digest,
            )
            return existing
        raise RuntimeError(
            "candidate artifact already exists for different model content"
        )

    response = bootstrap._request_json(
        api_url,
        api_token,
        "POST",
        f"/stores/{store_id}/authorization-models",
        model,
    )
    model_id = bootstrap._required_string(response, "authorization_model_id")
    bootstrap.validate_remote_model(
        api_url,
        api_token,
        store_id=store_id,
        model_id=model_id,
        expected_model=model,
        expected_digest=digest,
    )
    candidate = {
        "version": 1,
        "store_id": store_id,
        "authorization_model_id": model_id,
        "model_sha256": digest,
        "published_at": _now(),
    }
    _write_json(candidate_path, candidate)
    return candidate


def _active_config(path: Path) -> dict[str, Any]:
    config = _read_json(path)
    for key in ("store_id", "authorization_model_id", "model_sha256"):
        if not isinstance(config.get(key), str) or not config[key]:
            raise RuntimeError(f"active OpenFGA config is missing {key}")
    if not _CONTROL_PLANE_ID.fullmatch(config["store_id"]):
        raise RuntimeError("active OpenFGA store ID is invalid")
    if not _CONTROL_PLANE_ID.fullmatch(config["authorization_model_id"]):
        raise RuntimeError("active OpenFGA authorization model ID is invalid")
    if not _MODEL_DIGEST.fullmatch(config["model_sha256"]):
        raise RuntimeError("active OpenFGA model digest is invalid")
    return config


def _validate_source(source_sha: str, source_ref: str) -> None:
    if not _SOURCE_SHA.fullmatch(source_sha):
        raise RuntimeError("source SHA must be a full lowercase Git digest")
    if not _SOURCE_REF.fullmatch(source_ref):
        raise RuntimeError("source ref must be an immutable v* tag")


def _samples(path: Path) -> tuple[list[dict[str, Any]], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"invalid or missing canary samples: {path}") from exc
    if len(raw) > _MAX_SAMPLE_BYTES:
        raise RuntimeError("canary sample set exceeds the 8 MiB limit")
    rows: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(raw.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            row = json.loads(raw_line)
        except ValueError as exc:
            raise RuntimeError(f"invalid canary JSONL at line {line_number}") from exc
        if not isinstance(row, dict):
            raise RuntimeError(f"canary line {line_number} must be an object")
        for key in ("user", "relation", "object"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise RuntimeError(f"canary line {line_number} is missing {key}")
        expected_keys = {"expected_old", "expected_new"} & set(row)
        if expected_keys and expected_keys != {"expected_old", "expected_new"}:
            raise RuntimeError(
                f"canary line {line_number} must declare both expected results"
            )
        for key in expected_keys:
            if not isinstance(row[key], bool):
                raise RuntimeError(f"canary line {line_number} has non-boolean {key}")
        rows.append(row)
        if len(rows) > _MAX_SAMPLE_ROWS:
            raise RuntimeError("canary sample set exceeds the 5000 row limit")
    if not rows:
        raise RuntimeError("canary sample set must not be empty")
    return rows, hashlib.sha256(raw).hexdigest()


def _check(
    api_url: str,
    api_token: str,
    store_id: str,
    model_id: str,
    row: dict[str, Any],
) -> bool:
    response = bootstrap._request_json(
        api_url,
        api_token,
        "POST",
        f"/stores/{store_id}/check",
        {
            "tuple_key": {
                "user": row["user"],
                "relation": row["relation"],
                "object": row["object"],
            },
            "authorization_model_id": model_id,
            "consistency": "HIGHER_CONSISTENCY",
        },
    )
    allowed = response.get("allowed")
    if not isinstance(allowed, bool):
        raise RuntimeError("OpenFGA canary returned an invalid check result")
    return allowed


def _latency_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        index = min(len(ordered) - 1, int(len(ordered) * fraction))
        return round(ordered[index], 6)

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": round(ordered[-1], 6),
    }


def _validate_evidence_metrics(evidence: dict[str, Any]) -> None:
    sample_count = evidence.get("sample_count")
    divergence_count = evidence.get("divergence_count")
    failure_count = evidence.get("failure_count")
    if (
        evidence.get("version") != 1
        or not isinstance(sample_count, int)
        or isinstance(sample_count, bool)
        or not 0 < sample_count <= _MAX_SAMPLE_ROWS
        or not isinstance(divergence_count, int)
        or isinstance(divergence_count, bool)
        or not 0 <= divergence_count <= sample_count
        or failure_count != 0
        or not isinstance(evidence.get("checked_at"), str)
        or not evidence["checked_at"]
        or not isinstance(evidence.get("samples_sha256"), str)
        or not _MODEL_DIGEST.fullmatch(evidence["samples_sha256"])
    ):
        raise RuntimeError("canary evidence metrics are invalid")
    latency = evidence.get("latency_ms")
    if not isinstance(latency, dict):
        raise RuntimeError("canary evidence latency is invalid")
    for model_name in ("active", "candidate"):
        summary = latency.get(model_name)
        if not isinstance(summary, dict):
            raise RuntimeError("canary evidence latency is invalid")
        values: list[float] = []
        for percentile in ("p50", "p95", "p99", "max"):
            value = summary.get(percentile)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
                or value < 0
            ):
                raise RuntimeError("canary evidence latency is invalid")
            values.append(float(value))
        if values != sorted(values):
            raise RuntimeError("canary evidence latency is invalid")


def canary(
    *,
    config_path: Path,
    candidate_path: Path,
    samples_path: Path,
    evidence_path: Path,
    source_sha: str,
    source_ref: str,
) -> dict[str, Any]:
    _validate_source(source_sha, source_ref)
    api_url, api_token, store_id = _control_plane()
    active = _active_config(config_path)
    candidate = _read_json(candidate_path)
    if active["store_id"] != store_id or candidate.get("store_id") != store_id:
        raise RuntimeError("canary store does not match the pinned control plane")
    candidate_id = str(candidate.get("authorization_model_id") or "")
    candidate_digest = str(candidate.get("model_sha256") or "")
    if not candidate_id or not _CONTROL_PLANE_ID.fullmatch(candidate_id):
        raise RuntimeError("candidate authorization model ID is invalid")
    if not _MODEL_DIGEST.fullmatch(candidate_digest):
        raise RuntimeError("candidate model digest is invalid")
    active_remote_digest = bootstrap.remote_model_sha256(
        api_url,
        api_token,
        store_id=store_id,
        model_id=active["authorization_model_id"],
    )
    if active_remote_digest != active["model_sha256"]:
        raise RuntimeError("active OpenFGA model digest does not match its pin")
    candidate_remote_digest = bootstrap.remote_model_sha256(
        api_url,
        api_token,
        store_id=store_id,
        model_id=candidate_id,
    )
    if candidate_remote_digest != candidate_digest:
        raise RuntimeError("candidate OpenFGA model digest does not match its artifact")

    rows, samples_digest = _samples(samples_path)
    failures = 0
    divergences = 0
    active_latencies: list[float] = []
    candidate_latencies: list[float] = []
    for row in rows:
        started = time.perf_counter()
        old = _check(
            api_url,
            api_token,
            store_id,
            active["authorization_model_id"],
            row,
        )
        active_latencies.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        new = _check(api_url, api_token, store_id, candidate_id, row)
        candidate_latencies.append((time.perf_counter() - started) * 1000)
        if old != new:
            divergences += 1
        if "expected_old" in row:
            if old != row["expected_old"] or new != row["expected_new"]:
                failures += 1
        elif old != new:
            failures += 1

    evidence = {
        "version": 1,
        "status": "passed" if failures == 0 else "failed",
        "store_id": store_id,
        "active_authorization_model_id": active["authorization_model_id"],
        "active_model_sha256": active["model_sha256"],
        "candidate_authorization_model_id": candidate_id,
        "candidate_model_sha256": candidate_digest,
        "samples_sha256": samples_digest,
        "sample_count": len(rows),
        "divergence_count": divergences,
        "failure_count": failures,
        "latency_ms": {
            "active": _latency_summary(active_latencies),
            "candidate": _latency_summary(candidate_latencies),
        },
        "source_sha": source_sha,
        "source_ref": source_ref,
        "checked_at": _now(),
    }
    _write_json(evidence_path, evidence)
    if failures:
        raise RuntimeError(f"OpenFGA canary failed for {failures} sample(s)")
    return evidence


def promote(
    *,
    config_path: Path,
    candidate_path: Path,
    evidence_path: Path,
    model_path: Path,
    source_sha: str,
    source_ref: str,
) -> dict[str, Any]:
    _validate_source(source_sha, source_ref)
    api_url, api_token, store_id = _control_plane()
    candidate = _read_json(candidate_path)
    evidence = _read_json(evidence_path)
    model, digest = _load_model(model_path)
    with _config_lock(config_path):
        active = _active_config(config_path)
        required = {
            "status": "passed",
            "store_id": store_id,
            "active_authorization_model_id": active["authorization_model_id"],
            "active_model_sha256": active["model_sha256"],
            "candidate_authorization_model_id": candidate.get("authorization_model_id"),
            "candidate_model_sha256": candidate.get("model_sha256"),
            "source_sha": source_sha,
            "source_ref": source_ref,
        }
        for key, value in required.items():
            if evidence.get(key) != value:
                raise RuntimeError(f"canary evidence is stale or mismatched: {key}")
        _validate_evidence_metrics(evidence)
        if candidate.get("store_id") != store_id or digest != candidate.get(
            "model_sha256"
        ):
            raise RuntimeError("candidate does not match this release model")
        candidate_id = str(candidate["authorization_model_id"])
        bootstrap.validate_remote_model(
            api_url,
            api_token,
            store_id=store_id,
            model_id=candidate_id,
            expected_model=model,
            expected_digest=digest,
        )
        history = active.get("model_history")
        if not isinstance(history, list):
            history = []
        previous = {
            "authorization_model_id": active["authorization_model_id"],
            "model_sha256": active["model_sha256"],
            "retained_at": _now(),
        }
        history = [
            previous,
            *[
                item
                for item in history
                if isinstance(item, dict)
                and item.get("authorization_model_id")
                != previous["authorization_model_id"]
                and item.get("authorization_model_id") != candidate_id
            ],
        ][:10]
        promoted = {
            **active,
            "store_id": store_id,
            "authorization_model_id": candidate_id,
            "model_sha256": digest,
            "previous_authorization_model_id": previous["authorization_model_id"],
            "previous_model_sha256": previous["model_sha256"],
            "model_history": history,
            "last_model_promotion": {
                "source_sha": source_sha,
                "source_ref": source_ref,
                "samples_sha256": evidence["samples_sha256"],
                "sample_count": evidence["sample_count"],
                "divergence_count": evidence["divergence_count"],
                "latency_ms": evidence["latency_ms"],
                "promoted_at": _now(),
            },
        }
        _write_json(config_path, promoted)
    return promoted


def rollback(*, config_path: Path, model_id: str | None) -> dict[str, Any]:
    api_url, api_token, store_id = _control_plane()
    with _config_lock(config_path):
        active = _active_config(config_path)
        if active["store_id"] != store_id:
            raise RuntimeError("rollback store does not match the control plane")
        target_id = model_id or str(active.get("previous_authorization_model_id") or "")
        history = active.get("model_history")
        if not isinstance(history, list):
            history = []
        target = next(
            (
                item
                for item in history
                if isinstance(item, dict)
                and item.get("authorization_model_id") == target_id
            ),
            None,
        )
        if target is None or not isinstance(target.get("model_sha256"), str):
            raise RuntimeError("rollback target is not in retained model history")
        if not _CONTROL_PLANE_ID.fullmatch(target_id):
            raise RuntimeError("rollback target authorization model ID is invalid")
        target_digest = str(target["model_sha256"])
        if not _MODEL_DIGEST.fullmatch(target_digest):
            raise RuntimeError("rollback target model digest is invalid")
        try:
            remote_digest = bootstrap.remote_model_sha256(
                api_url,
                api_token,
                store_id=store_id,
                model_id=target_id,
            )
        except RuntimeError as exc:
            raise RuntimeError("rollback target no longer exists in OpenFGA") from exc
        if remote_digest != target_digest:
            raise RuntimeError("rollback target model digest does not match history")
        current = {
            "authorization_model_id": active["authorization_model_id"],
            "model_sha256": active["model_sha256"],
            "retained_at": _now(),
        }
        remaining = [
            item
            for item in history
            if isinstance(item, dict)
            and item.get("authorization_model_id") != target_id
            and item.get("authorization_model_id") != current["authorization_model_id"]
        ]
        rolled_back = {
            **active,
            "authorization_model_id": target_id,
            "model_sha256": target["model_sha256"],
            "previous_authorization_model_id": current["authorization_model_id"],
            "previous_model_sha256": current["model_sha256"],
            "model_history": [current, *remaining][:10],
            "last_model_rollback": {
                "from_authorization_model_id": current["authorization_model_id"],
                "to_authorization_model_id": target_id,
                "rolled_back_at": _now(),
            },
        }
        _write_json(config_path, rolled_back)
    return rolled_back


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-file",
        type=Path,
        default=Path(os.environ.get("OPENFGA_BOOTSTRAP_CONFIG_FILE", _DEFAULT_CONFIG)),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish_parser = subparsers.add_parser("publish")
    publish_parser.add_argument("--model", type=Path, default=_DEFAULT_MODEL)
    publish_parser.add_argument("--candidate-file", type=Path, required=True)

    canary_parser = subparsers.add_parser("canary")
    canary_parser.add_argument("--candidate-file", type=Path, required=True)
    canary_parser.add_argument("--samples", type=Path, required=True)
    canary_parser.add_argument("--evidence-file", type=Path, required=True)
    canary_parser.add_argument("--source-sha", required=True)
    canary_parser.add_argument("--source-ref", required=True)

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--candidate-file", type=Path, required=True)
    promote_parser.add_argument("--evidence-file", type=Path, required=True)
    promote_parser.add_argument("--model", type=Path, default=_DEFAULT_MODEL)
    promote_parser.add_argument("--source-sha", required=True)
    promote_parser.add_argument("--source-ref", required=True)

    rollback_parser = subparsers.add_parser("rollback")
    rollback_parser.add_argument("--model-id")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "publish":
        result = publish(model_path=args.model, candidate_path=args.candidate_file)
    elif args.command == "canary":
        result = canary(
            config_path=args.config_file,
            candidate_path=args.candidate_file,
            samples_path=args.samples,
            evidence_path=args.evidence_file,
            source_sha=args.source_sha,
            source_ref=args.source_ref,
        )
    elif args.command == "promote":
        result = promote(
            config_path=args.config_file,
            candidate_path=args.candidate_file,
            evidence_path=args.evidence_file,
            model_path=args.model,
            source_sha=args.source_sha,
            source_ref=args.source_ref,
        )
    else:
        result = rollback(config_path=args.config_file, model_id=args.model_id)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
