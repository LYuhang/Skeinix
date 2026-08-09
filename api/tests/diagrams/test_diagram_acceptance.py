from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from vibecanvas_api.diagrams.acceptance import (
    StageEvidence,
    Timeline,
    _timeline_valid,
    acceptance_matrix,
    load_acceptance_fixtures,
    verify_evidence,
)

FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "web/e2e/fixtures/diagram-acceptance.json"
)


def test_release_matrix_is_generated_from_every_registry_fixture() -> None:
    fixtures = load_acceptance_fixtures(FIXTURES)
    matrix = acceptance_matrix(fixtures)

    assert [fixture.key for fixture in fixtures] == [
        "architecture/system-container",
        "flow/basic",
    ]
    assert len(matrix) == 8
    assert {
        (item["family"], item["type"], item["runtime"], item["stage"])
        for item in matrix
    } == {
        (fixture.family, fixture.type, runtime, stage)
        for fixture in fixtures
        for runtime in ("langchain", "codex")
        for stage in ("create", "modify")
    }


def test_fixture_collection_fails_closed_on_registry_drift(tmp_path: Path) -> None:
    values = json.loads(FIXTURES.read_text())
    values.pop()
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text(json.dumps(values))

    with pytest.raises(ValueError, match="missing=.*flow/basic"):
        load_acceptance_fixtures(incomplete)


def test_empty_evidence_is_reported_as_unverified_not_skipped(
    tmp_path: Path,
) -> None:
    fixtures = load_acceptance_fixtures(FIXTURES)
    report, markdown = verify_evidence(fixtures, tmp_path)

    assert report["required_combinations"] == 8
    assert report["covered_combinations"] == 0
    assert report["passed_combinations"] == 0
    assert report["unverified_types"] == [
        "architecture/system-container",
        "flow/basic",
    ]
    assert markdown.count("unverified") == 8


def _artifact(path: Path, root: Path, revision: str | None) -> dict:
    data = path.read_bytes()
    return {
        "filename": str(path.relative_to(root)),
        "bytes": len(data),
        "sha256": f"sha256:{hashlib.sha256(data).hexdigest()}",
        "revision": revision,
    }


def _write_png(path: Path, color: str, size: tuple[int, int]) -> None:
    Image.new("RGB", size, color).save(path)


def test_atomic_release_evidence_binds_runtime_screenshots_and_light_exports(
    tmp_path: Path,
) -> None:
    fixtures = load_acceptance_fixtures(FIXTURES)
    source_fingerprint = f"sha256:{'a' * 64}"
    for fixture in fixtures:
        for runtime in ("langchain", "codex"):
            record_dir = tmp_path / f"{fixture.id}-{runtime}"
            export_dir = record_dir / "exports"
            export_dir.mkdir(parents=True)
            create_revision = f"sha256:{fixture.id}-{runtime}-create"
            modify_revision = f"sha256:{fixture.id}-{runtime}-modify"
            screenshot_files = {
                "before": ("before.png", None, "white"),
                "create_light_full": (
                    "create-light-full.png", create_revision, "white",
                ),
                "modify_light_full": (
                    "modify-light-full.png", modify_revision, "white",
                ),
                "refresh_light_full": (
                    "refresh-light-full.png", modify_revision, "white",
                ),
                "modify_dark_full": (
                    "modify-dark-full.png", modify_revision, "#17191d",
                ),
            }
            screenshots = {}
            for name, (filename, revision, color) in screenshot_files.items():
                path = record_dir / filename
                _write_png(path, color, (1280, 720))
                screenshots[name] = _artifact(path, record_dir, revision)

            export_paths = {
                "svg": export_dir / "diagram.svg",
                "png": export_dir / "diagram.png",
                "pdf": export_dir / "diagram.pdf",
            }
            export_paths["svg"].write_bytes(
                b'<svg><rect width="10" height="10" fill="#ffffff"/></svg>'
            )
            _write_png(export_paths["png"], "white", (640, 480))
            export_paths["pdf"].write_bytes(b"%PDF-1.4\n%%EOF\n")
            exports = {
                format_name: {
                    **_artifact(path, record_dir, modify_revision),
                    "format": format_name,
                    "theme": "light",
                    "background": "white",
                }
                for format_name, path in export_paths.items()
            }
            timeline = {
                "revision": create_revision,
                "T0": 1,
                "T1": 2,
                "T2": 3,
                "T3": 4,
            }
            evidence = {
                "schema_version": 2,
                "acceptance_run_id": tmp_path.name,
                "source_fingerprint": source_fingerprint,
                "fixture_id": fixture.id,
                "family": fixture.family,
                "type": fixture.type,
                "runtime": runtime,
                "runtime_evidence": {
                    "configured_runtime": runtime,
                    "model_label": f"real-{runtime}-model",
                },
                "recovery_turns": {"create": 0, "modify": 0},
                "review_outcomes": {
                    "create": {
                        "accepted": True,
                        "mode": "deliver",
                        "review_count": 1,
                        "warnings": [],
                    },
                    "modify": {
                        "accepted": True,
                        "mode": "deliver",
                        "review_count": 1,
                        "warnings": (
                            ["edge_crossing", "edge_crossing"]
                            if fixture.id == "flow-basic" and runtime == "codex"
                            else []
                        ),
                    },
                },
                "chat_id": f"chat-{fixture.id}-{runtime}",
                "command_catalog_version": "2026.08.1",
                "spec_hash": f"sha256:{'b' * 64}",
                "prompts": {"create": "create prompt", "modify": "modify prompt"},
                "agent_replies": {"create": "created", "modify": "modified"},
                "tool_trace": [{"name": "trace"}] * 8,
                "semantic_diff": {"preserved": True},
                "stages": {
                    "create": {
                        "status": "pass",
                        "revision": create_revision,
                        "source_path": "/data/diagrams/diagram.vdiagram.json",
                        "timeline": timeline,
                        "assertions": {"semantic": True},
                        "tool_names": [
                            "get_diagram_spec", "check_diagram",
                            "render_interactive", "review_diagram",
                        ],
                    },
                    "modify": {
                        "status": "pass",
                        "revision": modify_revision,
                        "source_path": "/data/diagrams/diagram.vdiagram.json",
                        "timeline": {
                            **timeline,
                            "revision": modify_revision,
                        },
                        "assertions": {"semantic": True},
                        "tool_names": [
                            "inspect_diagram", "check_diagram",
                            "render_interactive", "review_diagram",
                        ],
                    },
                },
                "screenshots": screenshots,
                "model_image_answers": {"create": "left", "modify": "left"},
                "accessibility": {
                    "reduced_motion": True,
                    "toolbar_named": True,
                    "semantic_summary": "diagram summary",
                    "keyboard_selection": True,
                },
                "startup_progress": {"first_visible_ms": 800},
                "exports": exports,
                "console_errors": [],
                "network_errors": [],
                "first_failure_trace": None,
            }
            (record_dir / "evidence.json").write_text(json.dumps(evidence))

    report, _ = verify_evidence(fixtures, tmp_path)
    assert report["passed_combinations"] == 8
    assert report["errors"] == []
    assert report["acceptance_run_id"] == tmp_path.name
    assert report["source_fingerprint"] == source_fingerprint


def test_timeline_accepts_independent_sse_and_preview_delivery_order() -> None:
    stage = StageEvidence(
        status="pass",
        revision="sha256:revision",
        source_path="/data/diagrams/diagram.vdiagram.json",
        timeline=Timeline(
            revision="sha256:revision",
            T0=1_000,
            T1=1_050,
            T2=1_020,
            T3=1_060,
        ),
        assertions={"semantic": True},
        tool_names=[
            "get_diagram_spec",
            "check_diagram",
            "render_interactive",
            "review_diagram",
        ],
    )

    assert _timeline_valid(stage)
