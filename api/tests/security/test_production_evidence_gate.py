from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/security/verify_production_evidence.py"
SECURITY_WORKFLOW = ROOT / ".github/workflows/security.yml"
PROMOTION_WORKFLOW = ROOT / ".github/workflows/production-evidence.yml"
PRODUCTION_RELEASE = ROOT / "scripts/deploy/production_release.sh"
SPEC = importlib.util.spec_from_file_location("production_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def _valid_manifest() -> dict:
    observed_at = "2026-08-02T12:00:00Z"
    gates = {}
    for index, gate in enumerate(module.REQUIRED_GATES):
        gates[gate] = {
            "status": "passed",
            "owner": f"owner-{index}",
            "verified_by": f"reviewer-{index}",
            "verified_at": observed_at,
            "evidence": [
                {
                    "kind": "test-record",
                    "uri": f"https://evidence.example.test/{gate}/record",
                    "immutable_id": f"record-{index}",
                    "observed_at": observed_at,
                }
            ],
        }
    return {
        "manifest_version": 1,
        "release": {
            "repository": "example/vibecanvas",
            "commit_sha": "a" * 40,
            "tag": "v1.2.3",
            "environment": "production",
        },
        "gates": gates,
    }


def _verify(manifest: dict, *, allow_pending: bool = False) -> list[str]:
    release = manifest["release"]
    return module.verify_manifest(
        manifest,
        allow_pending=allow_pending,
        max_age_days=180,
        expected_repository=(None if allow_pending else release["repository"]),
        expected_commit_sha=(None if allow_pending else release["commit_sha"]),
        expected_tag=(None if allow_pending else release["tag"]),
        now=datetime(2026, 8, 2, 13, tzinfo=timezone.utc),
    )


def test_complete_independently_reviewed_manifest_passes() -> None:
    errors = _verify(_valid_manifest())
    assert errors == []


def test_pending_or_missing_external_evidence_fails_closed() -> None:
    manifest = _valid_manifest()
    manifest["gates"]["kms_workload_identity"] = {
        "status": "pending",
        "owner": "Cloud Security",
        "verified_by": None,
        "verified_at": None,
        "evidence": [],
    }
    del manifest["gates"]["clamav_capacity"]
    errors = _verify(manifest)
    assert any("missing required gates: clamav_capacity" in item for item in errors)
    assert any("kms_workload_identity.status" in item for item in errors)


def test_secret_bearing_keys_and_self_approval_are_rejected() -> None:
    manifest = _valid_manifest()
    gate = manifest["gates"]["release_attestations"]
    gate["verified_by"] = gate["owner"]
    gate["evidence"][0]["token"] = "must-never-be-recorded"
    errors = _verify(manifest)
    assert any("forbidden secret-bearing keys" in item for item in errors)
    assert any("reviewer must differ from owner" in item for item in errors)


def test_checked_in_template_is_structurally_valid_but_not_release_ready() -> None:
    manifest = json.loads(
        (ROOT / "docs/production-evidence-manifest.example.json").read_text()
    )
    template_errors = _verify(manifest, allow_pending=True)
    release_errors = module.verify_manifest(
        manifest,
        allow_pending=False,
        max_age_days=180,
        expected_repository=manifest["release"]["repository"],
        expected_commit_sha=manifest["release"]["commit_sha"],
        expected_tag=manifest["release"]["tag"],
        now=datetime(2026, 8, 2, 13, tzinfo=timezone.utc),
    )
    assert template_errors == []
    assert len(
        [item for item in release_errors if "production gate requires passed" in item]
    ) == len(module.REQUIRED_GATES)


def test_manifest_is_bound_to_the_release_being_promoted() -> None:
    manifest = _valid_manifest()
    errors = module.verify_manifest(
        manifest,
        allow_pending=False,
        max_age_days=180,
        expected_repository="example/vibecanvas",
        expected_commit_sha="b" * 40,
        expected_tag="v1.2.4",
        now=datetime(2026, 8, 2, 13, tzinfo=timezone.utc),
    )
    assert any("release.commit_sha" in item for item in errors)
    assert any("release.tag" in item for item in errors)


def test_release_mode_requires_external_release_identity() -> None:
    errors = module.verify_manifest(
        _valid_manifest(),
        allow_pending=False,
        max_age_days=180,
        now=datetime(2026, 8, 2, 13, tzinfo=timezone.utc),
    )
    assert any("expected release repository is required" in item for item in errors)
    assert any("expected release commit_sha is required" in item for item in errors)
    assert any("expected release tag is required" in item for item in errors)


def test_unknown_fields_credential_urls_and_unbounded_age_are_rejected() -> None:
    manifest = _valid_manifest()
    artifact = manifest["gates"]["immutable_audit_sink"]["evidence"][0]
    artifact["extra"] = "not part of the reviewed schema"
    artifact["uri"] = "https://evidence.example.test/record?access_token=value"
    release = manifest["release"]
    errors = module.verify_manifest(
        manifest,
        allow_pending=False,
        max_age_days=181,
        expected_repository=release["repository"],
        expected_commit_sha=release["commit_sha"],
        expected_tag=release["tag"],
        now=datetime(2026, 8, 2, 13, tzinfo=timezone.utc),
    )
    assert any("max_age_days" in item for item in errors)
    assert any("unknown fields: extra" in item for item in errors)
    assert any("query, and fragment are forbidden" in item for item in errors)


def test_ci_and_protected_promotion_workflows_enforce_the_contract() -> None:
    security = SECURITY_WORKFLOW.read_text(encoding="utf-8")
    promotion = PROMOTION_WORKFLOW.read_text(encoding="utf-8")
    assert "production-evidence-contract:" in security
    assert "--allow-pending" in security
    assert "environment: production-release" in promotion
    assert "Reject an unprotected workflow ref" in promotion
    assert "git ls-files --error-unmatch" in promotion
    assert "git rev-parse" in promotion
    assert "git merge-base --is-ancestor" in promotion
    assert '--repository "$GITHUB_REPOSITORY"' in promotion
    assert '--commit-sha "$RELEASE_SHA"' in promotion
    assert '--tag "$RELEASE_TAG"' in promotion
    assert 'sha256sum "$EVIDENCE_MANIFEST"' in promotion
    assert (
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in promotion
    )


def test_production_entrypoint_cannot_start_before_both_release_gates() -> None:
    subprocess.run(["bash", "-n", str(PRODUCTION_RELEASE)], check=True)
    script = PRODUCTION_RELEASE.read_text(encoding="utf-8")
    verify_function = script.index("verify_release()")
    start_function = script.index("start_release()")
    verify_call = script.index("  verify_release\n", start_function)
    compose_up = script.index("      up -d --no-build --pull always --wait")
    assert verify_function < start_function < verify_call < compose_up
    assert 'for release_image in "$VIBECANVAS_API_IMAGE"' in script
    assert '"$ATTESTATION_VERIFIER"' in script
    assert '"$EVIDENCE_VERIFIER"' in script
    assert '--repository "$RELEASE_REPOSITORY"' in script
    assert '--commit-sha "$RELEASE_SHA"' in script
    assert '--tag "${RELEASE_REF#refs/tags/}"' in script
    assert "config --quiet" in script
