#!/usr/bin/env python3
"""Fail-closed verifier for external production security evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlparse


REQUIRED_GATES = (
    "tls_and_trusted_proxy",
    "kms_workload_identity",
    "production_openfga",
    "immutable_audit_sink",
    "backup_restore",
    "key_rotation",
    "historical_credential_rotation",
    "enterprise_idp_scim",
    "break_glass_two_person",
    "clamav_capacity",
    "release_attestations",
    "extension_release",
    "authorization_canary_rollback",
)
MAX_EVIDENCE_AGE_DAYS = 180
TOP_LEVEL_FIELDS = frozenset({"manifest_version", "release", "gates"})
RELEASE_FIELDS = frozenset({"repository", "commit_sha", "tag", "environment"})
GATE_FIELDS = frozenset(
    {
        "status",
        "owner",
        "verified_by",
        "verified_at",
        "evidence",
    }
)
ARTIFACT_FIELDS = frozenset(
    {
        "kind",
        "uri",
        "sha256",
        "immutable_id",
        "observed_at",
    }
)
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "token",
        "secret",
        "api_key",
        "access_key",
        "private_key",
        "credential_value",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _reject_unknown_fields(
    value: dict,
    *,
    allowed: frozenset[str],
    field: str,
) -> list[str]:
    unknown = sorted(set(value) - allowed)
    if not unknown:
        return []
    return [f"{field}: unknown fields: {', '.join(unknown)}"]


def _timestamp(value: object, field: str, errors: list[str]) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field}: RFC3339 timestamp is required")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field}: invalid RFC3339 timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field}: timezone is required")
        return None
    return parsed.astimezone(timezone.utc)


def _find_sensitive_keys(value: object, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in SENSITIVE_KEYS:
                findings.append(child_path)
            findings.extend(_find_sensitive_keys(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            findings.extend(_find_sensitive_keys(child, f"{path}[{index}]"))
    return findings


def _artifact_errors(
    artifact: object,
    *,
    field: str,
    now: datetime,
    oldest: datetime,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(artifact, dict):
        return [f"{field}: object is required"]
    errors.extend(
        _reject_unknown_fields(
            artifact,
            allowed=ARTIFACT_FIELDS,
            field=field,
        )
    )
    kind = artifact.get("kind")
    if not isinstance(kind, str) or not kind.strip():
        errors.append(f"{field}.kind: non-empty value is required")
    uri = artifact.get("uri")
    if not isinstance(uri, str) or not uri.strip():
        errors.append(f"{field}.uri: immutable evidence URI is required")
    else:
        parsed = urlparse(uri)
        if parsed.scheme not in {"https", "s3", "gs", "oci"}:
            errors.append(f"{field}.uri: scheme must be https, s3, gs, or oci")
        if parsed.scheme in {"https", "s3", "gs", "oci"} and not parsed.netloc:
            errors.append(f"{field}.uri: absolute evidence URI is required")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            errors.append(
                f"{field}.uri: credentials, query, and fragment are forbidden"
            )
    digest = artifact.get("sha256")
    immutable_id = artifact.get("immutable_id")
    if digest is not None and not (
        isinstance(digest, str) and SHA256_RE.fullmatch(digest)
    ):
        errors.append(f"{field}.sha256: lowercase 64-character digest required")
    if not digest and not (isinstance(immutable_id, str) and immutable_id.strip()):
        errors.append(f"{field}: sha256 or immutable_id is required")
    observed = _timestamp(artifact.get("observed_at"), f"{field}.observed_at", errors)
    if observed is not None:
        if observed > now + timedelta(minutes=5):
            errors.append(f"{field}.observed_at: timestamp is in the future")
        if observed < oldest:
            errors.append(f"{field}.observed_at: evidence is stale")
    return errors


def verify_manifest(
    manifest: object,
    *,
    allow_pending: bool,
    max_age_days: int,
    expected_repository: str | None = None,
    expected_commit_sha: str | None = None,
    expected_tag: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["$: JSON object is required"]
    if not 1 <= max_age_days <= MAX_EVIDENCE_AGE_DAYS:
        errors.append(f"max_age_days: expected 1..{MAX_EVIDENCE_AGE_DAYS}")
    errors.extend(
        _reject_unknown_fields(
            manifest,
            allowed=TOP_LEVEL_FIELDS,
            field="$",
        )
    )
    sensitive = _find_sensitive_keys(manifest)
    if sensitive:
        errors.append(
            "manifest contains forbidden secret-bearing keys: " + ", ".join(sensitive)
        )
    if manifest.get("manifest_version") != 1:
        errors.append("manifest_version: expected 1")
    release = manifest.get("release")
    if not isinstance(release, dict):
        errors.append("release: object is required")
    else:
        errors.extend(
            _reject_unknown_fields(
                release,
                allowed=RELEASE_FIELDS,
                field="release",
            )
        )
        repository = release.get("repository")
        if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
            errors.append("release.repository: expected owner/repository")
        commit_sha = release.get("commit_sha")
        if not isinstance(commit_sha, str) or not COMMIT_RE.fullmatch(commit_sha):
            errors.append("release.commit_sha: exact 40-character Git SHA required")
        release_tag = release.get("tag")
        if not isinstance(release_tag, str) or not TAG_RE.fullmatch(release_tag):
            errors.append("release.tag: immutable semantic v* tag required")
        if release.get("environment") != "production":
            errors.append("release.environment: expected production")
        expected_release = {
            "repository": expected_repository,
            "commit_sha": expected_commit_sha,
            "tag": expected_tag,
        }
        for field_name, expected_value in expected_release.items():
            if expected_value is None:
                if not allow_pending:
                    errors.append(
                        f"expected release {field_name} is required by the release gate"
                    )
            elif release.get(field_name) != expected_value:
                errors.append(
                    f"release.{field_name}: does not match the release being promoted"
                )
    gates = manifest.get("gates")
    if not isinstance(gates, dict):
        errors.append("gates: object is required")
        return errors
    missing = sorted(set(REQUIRED_GATES) - set(gates))
    unknown = sorted(set(gates) - set(REQUIRED_GATES))
    if missing:
        errors.append("gates: missing required gates: " + ", ".join(missing))
    if unknown:
        errors.append("gates: unknown gates: " + ", ".join(unknown))
    current = now or datetime.now(timezone.utc)
    bounded_max_age_days = min(
        max(max_age_days, 1),
        MAX_EVIDENCE_AGE_DAYS,
    )
    oldest = current - timedelta(days=bounded_max_age_days)
    for gate_name in REQUIRED_GATES:
        gate = gates.get(gate_name)
        field = f"gates.{gate_name}"
        if not isinstance(gate, dict):
            continue
        errors.extend(
            _reject_unknown_fields(
                gate,
                allowed=GATE_FIELDS,
                field=field,
            )
        )
        status = gate.get("status")
        if status not in {"pending", "passed"}:
            errors.append(f"{field}.status: expected pending or passed")
        if not allow_pending and status != "passed":
            errors.append(f"{field}.status: production gate requires passed")
        owner = gate.get("owner")
        verifier = gate.get("verified_by")
        if not isinstance(owner, str) or not owner.strip():
            errors.append(f"{field}.owner: accountable owner is required")
        if status == "passed":
            if not isinstance(verifier, str) or not verifier.strip():
                errors.append(f"{field}.verified_by: independent reviewer is required")
            elif (
                isinstance(owner, str)
                and verifier.strip().casefold() == owner.strip().casefold()
            ):
                errors.append(f"{field}.verified_by: reviewer must differ from owner")
            verified_at = _timestamp(
                gate.get("verified_at"),
                f"{field}.verified_at",
                errors,
            )
            if verified_at is not None:
                if verified_at > current + timedelta(minutes=5):
                    errors.append(f"{field}.verified_at: timestamp is in the future")
                if verified_at < oldest:
                    errors.append(f"{field}.verified_at: verification is stale")
            artifacts = gate.get("evidence")
            if not isinstance(artifacts, list) or not artifacts:
                errors.append(f"{field}.evidence: at least one artifact is required")
            else:
                for index, artifact in enumerate(artifacts):
                    errors.extend(
                        _artifact_errors(
                            artifact,
                            field=f"{field}.evidence[{index}]",
                            now=current,
                            oldest=oldest,
                        )
                    )
        elif not isinstance(gate.get("evidence", []), list):
            errors.append(f"{field}.evidence: list is required")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify fail-closed production security evidence",
    )
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="validate an evidence template without passing the release gate",
    )
    parser.add_argument("--max-age-days", type=int, default=180)
    parser.add_argument(
        "--repository",
        help="exact owner/repository being promoted (required in release mode)",
    )
    parser.add_argument(
        "--commit-sha",
        help="exact 40-character commit SHA being promoted (required in release mode)",
    )
    parser.add_argument(
        "--tag",
        help="exact semantic v* tag being promoted (required in release mode)",
    )
    args = parser.parse_args()
    if not 1 <= args.max_age_days <= MAX_EVIDENCE_AGE_DAYS:
        parser.error(f"--max-age-days must be between 1 and {MAX_EVIDENCE_AGE_DAYS}")
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"production_evidence_gate=fail error={exc}", file=sys.stderr)
        return 2
    errors = verify_manifest(
        manifest,
        allow_pending=args.allow_pending,
        max_age_days=args.max_age_days,
        expected_repository=args.repository,
        expected_commit_sha=args.commit_sha,
        expected_tag=args.tag,
    )
    if errors:
        print("production_evidence_gate=fail", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    mode = "template" if args.allow_pending else "release"
    print(
        f"production_evidence_gate=pass mode={mode} "
        f"required_gates={len(REQUIRED_GATES)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
