"""Registry-driven release matrix and evidence verifier for Agent Diagram."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .registry import REGISTRY_VERSION, list_enabled_types

_REQUIRED_STAGE_TOOLS = {
    "create": {
        "get_diagram_spec",
        "check_diagram",
        "render_interactive",
        "review_diagram",
    },
    "modify": {
        "inspect_diagram",
        "check_diagram",
        "render_interactive",
        "review_diagram",
    },
}
_REQUIRED_STAGES = {"create", "modify"}
_REQUIRED_SCREENSHOTS = {
    "before",
    "create_light_full",
    "modify_light_full",
    "refresh_light_full",
    "modify_dark_full",
}


class AcceptanceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateAssertions(AcceptanceModel):
    required_elements: list[str] = Field(min_length=2)
    required_relations: list[str] = Field(default_factory=list)


class ModifyAssertions(AcceptanceModel):
    added_elements: list[str] = Field(min_length=1)
    removed_or_replaced_elements: list[str] = Field(default_factory=list)
    preserved_elements: list[str] = Field(min_length=1)
    preserve_stable_ids: bool = True
    preserve_mental_map: bool = True


class PreviewAssertions(AcceptanceModel):
    inline_visible: bool = True
    side_panel_requires_user_action: bool = True
    no_manual_refresh: bool = True
    create_revision_visible: bool = True
    modify_revision_visible: bool = True


class DiagramAcceptanceFixture(AcceptanceModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    family: str
    type: str
    create_prompt: str = Field(min_length=20)
    create_assertions: CreateAssertions
    modify_prompt: str = Field(min_length=20)
    modify_assertions: ModifyAssertions
    preview_assertions: PreviewAssertions
    visual_question: str = Field(min_length=10)

    @property
    def key(self) -> str:
        return f"{self.family}/{self.type}"


class Timeline(AcceptanceModel):
    revision: str = Field(min_length=1)
    T0: float
    T1: float
    T2: float
    T3: float


class StageEvidence(AcceptanceModel):
    status: Literal["pass", "fail"]
    revision: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    timeline: Timeline
    assertions: dict[str, bool]
    tool_names: list[str] = Field(min_length=4)


class AccessibilityEvidence(AcceptanceModel):
    reduced_motion: bool
    toolbar_named: bool
    semantic_summary: str = Field(min_length=1)
    keyboard_selection: bool


class StartupProgressEvidence(AcceptanceModel):
    first_visible_ms: float = Field(ge=0, le=2_000)


class FileEvidence(AcceptanceModel):
    filename: str = Field(min_length=1, max_length=255)
    bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    revision: str | None = None


class ExportEvidence(FileEvidence):
    format: Literal["svg", "png", "pdf"]
    theme: Literal["light"]
    background: Literal["white"]


class RuntimeEvidence(AcceptanceModel):
    configured_runtime: Literal["langchain", "codex"]
    model_label: str = Field(min_length=1)


class RecoveryEvidence(AcceptanceModel):
    create: int = Field(ge=0, le=2)
    modify: int = Field(ge=0, le=2)


class ReviewOutcomeEvidence(AcceptanceModel):
    accepted: bool
    mode: Literal["deliver", "bounded_warning", "incomplete"]
    review_count: int = Field(ge=1, le=10)
    # A delivered diagram can legitimately disclose several non-blocking
    # warning instances (including the same code on different edges).  The
    # stricter single-warning rule belongs only to ``bounded_warning`` and is
    # enforced by ``_review_outcomes_valid`` below.
    warnings: list[str] = Field(max_length=64)


class DiagramAcceptanceEvidence(AcceptanceModel):
    schema_version: Literal[2]
    acceptance_run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_fingerprint: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    fixture_id: str
    family: str
    type: str
    runtime: Literal["langchain", "codex"]
    runtime_evidence: RuntimeEvidence
    recovery_turns: RecoveryEvidence
    review_outcomes: dict[Literal["create", "modify"], ReviewOutcomeEvidence]
    chat_id: str = Field(min_length=1)
    command_catalog_version: str = Field(min_length=1)
    spec_hash: str = Field(min_length=1)
    prompts: dict[Literal["create", "modify"], str]
    agent_replies: dict[Literal["create", "modify"], str]
    tool_trace: list[dict[str, Any]] = Field(min_length=8)
    semantic_diff: dict[str, Any]
    stages: dict[Literal["create", "modify"], StageEvidence]
    screenshots: dict[
        Literal[
            "before",
            "create_light_full",
            "modify_light_full",
            "refresh_light_full",
            "modify_dark_full",
        ],
        FileEvidence,
    ]
    model_image_answers: dict[Literal["create", "modify"], str]
    accessibility: AccessibilityEvidence
    startup_progress: StartupProgressEvidence
    exports: dict[Literal["svg", "png", "pdf"], ExportEvidence]
    console_errors: list[str]
    network_errors: list[str]
    first_failure_trace: str | None = None


def _release_registry_keys() -> list[str]:
    return [
        item.key
        for item in list_enabled_types()
        if item.maturity in {"preview", "ga"}
    ]


def load_acceptance_fixtures(path: str | Path) -> list[DiagramAcceptanceFixture]:
    fixture_path = Path(path)
    try:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixtures = [DiagramAcceptanceFixture.model_validate(item) for item in raw]
    except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise ValueError(f"invalid diagram acceptance fixtures: {exc}") from exc
    keys = [fixture.key for fixture in fixtures]
    if len(keys) != len(set(keys)):
        raise ValueError("diagram acceptance fixtures contain duplicate family/type")
    expected = set(_release_registry_keys())
    actual = set(keys)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(
            "diagram acceptance fixtures do not match release Registry; "
            f"missing={missing}, extra={extra}"
        )
    return sorted(fixtures, key=lambda item: item.key)


def acceptance_matrix(fixtures: list[DiagramAcceptanceFixture]) -> list[dict[str, str]]:
    return [
        {
            "fixture_id": fixture.id,
            "family": fixture.family,
            "type": fixture.type,
            "runtime": runtime,
            "stage": stage,
        }
        for fixture in fixtures
        for runtime in ("langchain", "codex")
        for stage in ("create", "modify")
    ]


def _timeline_valid(stage: StageEvidence) -> bool:
    timeline = stage.timeline
    return bool(
        timeline.revision == stage.revision
        and timeline.T0 <= timeline.T1
        and timeline.T0 <= timeline.T2 <= timeline.T3
        and max(timeline.T1, timeline.T3) - timeline.T0 <= 2000
    )


def _review_outcomes_valid(record: DiagramAcceptanceEvidence) -> bool:
    if set(record.review_outcomes) != _REQUIRED_STAGES:
        return False
    for stage_name, outcome in record.review_outcomes.items():
        if not outcome.accepted or outcome.mode == "incomplete":
            return False
        # ``deliver`` is the Diagram review protocol's authoritative signal
        # that the revision is ready. It may disclose non-blocking warning
        # instances; the browser acceptance test separately requires the final
        # Scene issues to match them exactly. The one-warning ceiling applies
        # only to the bounded recovery escape hatch.
        if (
            outcome.mode == "bounded_warning"
            and (
                record.recovery_turns.model_dump()[stage_name] < 2
                or len(outcome.warnings) != 1
            )
        ):
            return False
    return True


def _file_valid(root: Path, artifact: FileEvidence) -> bool:
    path = (root / artifact.filename).resolve()
    try:
        path.relative_to(root.resolve())
        data = path.read_bytes()
    except (OSError, ValueError):
        return False
    return bool(
        len(data) == artifact.bytes
        and f"sha256:{hashlib.sha256(data).hexdigest()}" == artifact.sha256
    )


def _screenshot_valid(root: Path, artifact: FileEvidence) -> bool:
    if not artifact.filename.lower().endswith(".png"):
        return False
    if not _file_valid(root, artifact):
        return False
    try:
        with Image.open(root / artifact.filename) as image:
            return image.width >= 1280 and image.height >= 720
    except (OSError, ValueError):
        return False


def _export_valid(root: Path, artifact: ExportEvidence) -> bool:
    if not artifact.filename.lower().endswith(f".{artifact.format}"):
        return False
    if artifact.theme != "light" or artifact.background != "white":
        return False
    if not _file_valid(root, artifact):
        return False
    data = (root / artifact.filename).read_bytes()
    if artifact.format == "svg":
        return b'fill="#ffffff"' in data and b"#17191d" not in data
    if artifact.format == "pdf":
        return data.startswith(b"%PDF")
    try:
        with Image.open(BytesIO(data)) as image:
            pixel = image.convert("RGB").getpixel((0, 0))
            return image.width > 0 and image.height > 0 and pixel == (255, 255, 255)
    except (OSError, ValueError):
        return False


def verify_evidence(
    fixtures: list[DiagramAcceptanceFixture],
    evidence_dir: str | Path,
) -> tuple[dict[str, Any], str]:
    root = Path(evidence_dir)
    records: dict[tuple[str, str], DiagramAcceptanceEvidence] = {}
    record_paths: dict[tuple[str, str], Path] = {}
    errors: list[str] = []
    for path in sorted(root.glob("**/evidence.json")):
        try:
            record = DiagramAcceptanceEvidence.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            errors.append(f"{path}: {exc}")
            continue
        key = (f"{record.family}/{record.type}", record.runtime)
        if key in records:
            errors.append(
                f"duplicate evidence for {key} in one atomic run: "
                f"{record_paths[key]} and {path}"
            )
            continue
        records[key] = record
        record_paths[key] = path

    run_ids = {record.acceptance_run_id for record in records.values()}
    fingerprints = {record.source_fingerprint for record in records.values()}
    if records and run_ids != {root.name}:
        errors.append(
            f"evidence run IDs {sorted(run_ids)} do not match directory {root.name!r}"
        )
    if len(fingerprints) > 1:
        errors.append("evidence records do not share one source fingerprint")

    required_pairs = {
        (fixture.key, runtime)
        for fixture in fixtures
        for runtime in ("langchain", "codex")
    }
    rows: list[dict[str, Any]] = []
    passed_combinations = 0
    for fixture in fixtures:
        row: dict[str, Any] = {"type": fixture.key, "evidence": []}
        for runtime in ("langchain", "codex"):
            record = records.get((fixture.key, runtime))
            if record is not None and record.fixture_id != fixture.id:
                errors.append(
                    f"fixture id mismatch for {fixture.key}/{runtime}: "
                    f"{record.fixture_id} != {fixture.id}"
                )
            for stage_name in ("create", "modify"):
                cell = f"{runtime}_{stage_name}"
                if record is None:
                    row[cell] = "unverified"
                    continue
                stage = record.stages.get(stage_name)
                passed = bool(
                    stage
                    and stage.status == "pass"
                    and _timeline_valid(stage)
                    and stage.assertions
                    and all(stage.assertions.values())
                    and record.model_image_answers.get(stage_name)
                    and _REQUIRED_STAGE_TOOLS[stage_name].issubset(
                        stage.tool_names
                    )
                    and record.command_catalog_version == REGISTRY_VERSION
                    and record.runtime_evidence.configured_runtime == runtime
                    and bool(record.runtime_evidence.model_label.strip())
                    and _review_outcomes_valid(record)
                    and set(record.stages) == _REQUIRED_STAGES
                    and set(record.prompts) == _REQUIRED_STAGES
                    and set(record.agent_replies) == _REQUIRED_STAGES
                    and set(record.model_image_answers) == _REQUIRED_STAGES
                    and set(record.screenshots) == _REQUIRED_SCREENSHOTS
                    and record.accessibility.reduced_motion
                    and record.accessibility.toolbar_named
                    and record.accessibility.keyboard_selection
                    and bool(record.accessibility.semantic_summary.strip())
                    and set(record.exports) == {"svg", "png", "pdf"}
                    and all(
                        artifact.format == format_name
                        and artifact.revision == record.stages["modify"].revision
                        and _export_valid(
                            record_paths[(fixture.key, runtime)].parent,
                            artifact,
                        )
                        for format_name, artifact in record.exports.items()
                    )
                    and all(
                        _screenshot_valid(
                            record_paths[(fixture.key, runtime)].parent,
                            artifact,
                        )
                        for artifact in record.screenshots.values()
                    )
                    and record.screenshots["before"].revision is None
                    and record.screenshots["create_light_full"].revision
                    == record.stages["create"].revision
                    and all(
                        record.screenshots[name].revision
                        == record.stages["modify"].revision
                        for name in (
                            "modify_light_full",
                            "refresh_light_full",
                            "modify_dark_full",
                        )
                    )
                    and not record.console_errors
                    and not record.network_errors
                )
                row[cell] = "pass" if passed else "fail"
                if passed:
                    passed_combinations += 1
            if record is not None:
                row["evidence"].append(
                    str(record_paths[(fixture.key, runtime)].relative_to(root))
                )
        rows.append(row)

    unexpected = sorted(set(records) - required_pairs)
    if unexpected:
        errors.append(f"unexpected evidence records: {unexpected}")
    required_combinations = len(fixtures) * 2 * 2
    unverified = sorted(
        fixture.key
        for fixture in fixtures
        if any(records.get((fixture.key, runtime)) is None for runtime in (
            "langchain", "codex"
        ))
    )
    report = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "acceptance_run_id": next(iter(run_ids), None),
        "source_fingerprint": next(iter(fingerprints), None),
        "evidence_dir": str(root),
        "required_combinations": required_combinations,
        "covered_combinations": sum(
            1
            for row in rows
            for key, value in row.items()
            if key.endswith(("_create", "_modify")) and value != "unverified"
        ),
        "passed_combinations": passed_combinations,
        "unverified_types": unverified,
        "errors": errors,
        "evidence_files": [str(path) for path in record_paths.values()],
        "rows": rows,
    }
    headers = [
        "Family/Type", "LangChain create", "LangChain modify/live Preview",
        "Codex create", "Codex modify/live Preview", "Evidence",
    ]
    markdown = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        markdown.append("| " + " | ".join([
            f"`{row['type']}`",
            row["langchain_create"],
            row["langchain_modify"],
            row["codex_create"],
            row["codex_modify"],
            "<br>".join(row["evidence"]),
        ]) + " |")
    return report, "\n".join(markdown) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("manifest", "verify"))
    parser.add_argument("--fixtures", required=True)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--report-dir")
    args = parser.parse_args()
    fixtures = load_acceptance_fixtures(args.fixtures)
    if args.command == "manifest":
        print(json.dumps({
            "registry_version": REGISTRY_VERSION,
            "fixtures": [item.model_dump(mode="json") for item in fixtures],
            "matrix": acceptance_matrix(fixtures),
        }, ensure_ascii=False))
        return 0
    if not args.evidence_dir or not args.report_dir:
        parser.error("verify requires --evidence-dir and --report-dir")
    report, markdown = verify_evidence(fixtures, args.evidence_dir)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "matrix.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (report_dir / "matrix.md").write_text(markdown, encoding="utf-8")
    print(markdown, end="")
    return 0 if (
        report["passed_combinations"] == report["required_combinations"]
        and not report["unverified_types"]
        and not report["errors"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
