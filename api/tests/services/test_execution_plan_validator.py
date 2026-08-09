from __future__ import annotations

import json

from vibecanvas_api.services.execution_plans.validator import validate_plan_bytes


def _plan() -> dict:
    return {
        "schema_version": 1,
        "title": "Review readiness",
        "nodes": [
            {"id": "start", "type": "start", "next": ["official", "code"]},
            {
                "id": "official", "type": "subagent", "title": "Official",
                "task": "Write findings to /data/plan-work/official.md.",
                "next": ["synthesize"],
            },
            {
                "id": "code", "type": "subagent", "title": "Code",
                "task": "Write findings to /data/plan-work/code.md.",
                "next": ["synthesize"],
            },
            {
                "id": "synthesize", "type": "subagent", "title": "Synthesize",
                "task": "Read the two fixed files and write /data/plan-work/final.md.",
                "next": ["finish"],
            },
            {"id": "finish", "type": "end"},
        ],
        "budgets": {
            "max_wall_time_seconds": 1800,
        },
    }


def _bytes(value: dict) -> bytes:
    return json.dumps(value).encode()


def test_accepts_static_structured_fork_join_contract():
    report = validate_plan_bytes("/data/plans/readiness.plan.json", _bytes(_plan()))
    assert report.status == "valid"
    assert report.errors == []
    assert report.definition is not None


def test_rejects_removed_dataflow_and_parallel_fields():
    plan = _plan()
    plan["nodes"][1]["inputs"] = {"topic": {"from": "run.input.topic"}}
    plan["nodes"][1]["output_schema"] = {"type": "string"}
    plan["nodes"][1]["max_turns"] = 10
    report = validate_plan_bytes("/data/plans/readiness.plan.json", _bytes(plan))
    assert report.status == "invalid"
    assert {item.code for item in report.errors} == {"schema_validation_failed"}


def test_rejects_duplicate_json_keys_without_echoing_source():
    report = validate_plan_bytes(
        "/data/plans/readiness.plan.json",
        b'{"schema_version":1,"schema_version":1}',
    )
    assert report.status == "invalid"
    assert report.errors[0].code == "duplicate_json_key"


def test_rejects_cycle_unknown_target_and_scalar_next():
    plan = _plan()
    plan["nodes"][3]["next"] = ["start"]
    report = validate_plan_bytes("/data/plans/readiness.plan.json", _bytes(plan))
    assert "control_cycle" in {error.code for error in report.errors}

    plan = _plan()
    plan["nodes"][1]["next"] = ["missing"]
    report = validate_plan_bytes("/data/plans/readiness.plan.json", _bytes(plan))
    assert "unknown_control_target" in {error.code for error in report.errors}

    plan = _plan()
    plan["nodes"][1]["next"] = "synthesize"
    report = validate_plan_bytes("/data/plans/readiness.plan.json", _bytes(plan))
    assert "schema_validation_failed" in {error.code for error in report.errors}


def test_rejects_crossing_parallel_regions_before_their_merge():
    plan = {
        "schema_version": 1,
        "title": "Crossing graph",
        "nodes": [
            {"id": "start", "type": "start", "next": ["left", "right"]},
            {"id": "left", "type": "subagent", "title": "Left", "task": "Left", "next": ["inner_a", "shared"]},
            {"id": "right", "type": "subagent", "title": "Right", "task": "Right", "next": ["shared"]},
            {"id": "inner_a", "type": "subagent", "title": "Inner", "task": "Inner", "next": ["join"]},
            {"id": "shared", "type": "subagent", "title": "Shared", "task": "Shared", "next": ["join"]},
            {"id": "join", "type": "subagent", "title": "Join", "task": "Join", "next": ["end"]},
            {"id": "end", "type": "end"},
        ],
        "budgets": {"max_wall_time_seconds": 300},
    }
    report = validate_plan_bytes("/data/plans/crossing.plan.json", _bytes(plan))
    assert "split_branches_overlap_before_merge" in {item.code for item in report.errors}


def test_rejects_path_and_removed_budget_fields():
    report = validate_plan_bytes("/memory/readiness.plan.json", _bytes(_plan()))
    assert report.errors[0].code == "invalid_plan_path"
    plan = _plan()
    plan["budgets"]["max_parallelism"] = 2
    report = validate_plan_bytes("/data/plans/readiness.plan.json", _bytes(plan))
    assert "schema_validation_failed" in {error.code for error in report.errors}
