# -*- coding: utf-8 -*-
"""Canonical engine-event to ``EXEC_UPDATE`` mapper.

Pure-function unit tests (no DB, no event loop, no app). Covers every
engine status shape + the M3 ``default=str`` non-serializable-output
degradation.
"""

from __future__ import annotations

import json

from vibecanvas_api.services.exec_events import to_exec_update
from vibecanvas_api.routes.executions import _terminal_node_updates_from_result


EXEC = "e_abc"


def test_running_maps_to_running_frame():
    name, payload = to_exec_update(
        {"status": "running", "output": None, "error_message": "",
         "node_id": "node_3", "node_name": "n", "node_type": "CodeNode"},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload == {
        "exec_id": EXEC,
        "node_id": "node_3",
        "node_name": "n",
        "node_type": "CodeNode",
        "status": "running",
    }


def test_success_maps_to_completed_with_json_result_and_inputs():
    name, payload = to_exec_update(
        {"status": "success", "output": {"y": 42}, "error_message": "",
         "inputs": {"x": 1}, "node_id": "node_3",
         "node_name": "n", "node_type": "CodeNode"},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload["exec_id"] == EXEC
    assert payload["node_id"] == "node_3"
    assert payload["status"] == "completed"
    assert json.loads(payload["result"]) == {"y": 42}
    assert payload["inputs"] == {"x": 1}
    assert payload["node_name"] == "n"
    assert payload["node_type"] == "CodeNode"


def test_completed_maps_to_completed_with_json_result_and_inputs():
    name, payload = to_exec_update(
        {"status": "completed", "result": {"y": 42}, "error_message": "",
         "inputs": {"x": 1}, "node_id": "node_3"},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload["exec_id"] == EXEC
    assert payload["node_id"] == "node_3"
    assert payload["status"] == "completed"
    assert json.loads(payload["result"]) == {"y": 42}
    assert payload["inputs"] == {"x": 1}


def test_success_carries_per_node_duration_from_execution_time():
    """UX-3: the engine stamps ``execution_time`` (wall-clock seconds) on the
    success envelope; the mapper surfaces it as ``duration`` on the per-node
    frame so the Run output can show each node's timing."""
    name, payload = to_exec_update(
        {"status": "success", "output": {"y": 1}, "inputs": {},
         "node_id": "node_3", "execution_time": 0.42},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload["duration"] == 0.42


def test_success_without_execution_time_yields_none_duration():
    """An envelope without timing degrades to ``duration=None`` (back-compat),
    never raises."""
    _, payload = to_exec_update(
        {"status": "success", "output": {}, "inputs": {}, "node_id": "node_3"},
        EXEC,
    )
    assert payload["duration"] is None


def test_success_with_non_serializable_output_uses_default_str():
    """M3: a non-JSON output (e.g. a set / object) must degrade via
    default=str, NOT raise and tear down the stream."""
    class Weird:
        def __repr__(self):
            return "<weird>"

    mapped = to_exec_update(
        {"status": "success", "output": {"v": {1, 2}, "obj": Weird()},
         "inputs": {}, "node_id": "node_9"},
        EXEC,
    )
    assert mapped is not None
    _, payload = mapped
    # Must be a valid JSON string (did not raise).
    decoded = json.loads(payload["result"])
    assert "v" in decoded and "obj" in decoded
    # The object degraded to its str().
    assert decoded["obj"] == "<weird>"


def test_node_error_maps_to_error_frame():
    name, payload = to_exec_update(
        {"status": "error", "output": None, "node_id": "node_5",
         "error_message": "[NodeId: node_5] boom", "inputs": {"x": 7},
         "node_name": "broken", "node_type": "CodeNode"},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload == {
        "exec_id": EXEC, "node_id": "node_5", "status": "error",
        "node_name": "broken", "node_type": "CodeNode",
        "error": "[NodeId: node_5] boom",
        "inputs": {"x": 7},
    }


def test_engine_error_without_node_id_maps_to_terminal_error():
    name, payload = to_exec_update(
        {"status": "error", "error_message": "Engine critical error: x"},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload == {
        "exec_id": EXEC, "status": "error",
        "error": "Engine critical error: x",
    }
    assert "node_id" not in payload


def test_finished_with_errors_maps_to_terminal_error():
    name, payload = to_exec_update(
        {"status": "finished",
         "final_outputs": {"__end__": {"a": 1}, "other": {"b": 2}},
         "error_dict": {"node_2": "oops"}, "execution_time": 1.23},
        EXEC,
    )
    assert name == "EXEC_UPDATE"
    assert payload == {
        "exec_id": EXEC, "status": "error",
        "outputs": {"a": 1}, "errors": {"node_2": "oops"}, "duration": 1.23,
    }


def test_finished_without_end_scope_yields_empty_outputs():
    _, payload = to_exec_update(
        {"status": "finished", "final_outputs": {}, "error_dict": {}},
        EXEC,
    )
    assert payload["outputs"] == {}
    assert payload["errors"] == {}
    assert payload["status"] == "completed"


def test_unrecognized_status_returns_none():
    assert to_exec_update({"status": "weird"}, EXEC) is None
    assert to_exec_update({}, EXEC) is None


def test_terminal_result_backfills_missing_node_frames():
    wf = {
        "node_1": {"node_name": "__start__", "node_type": "StartNode"},
        "node_2": {"node_name": "parse", "node_type": "CodeNode"},
        "node_3": {"node_name": "__end__", "node_type": "EndNode"},
    }
    per_node = {"node_1": {"status": "running"}}

    updates = _terminal_node_updates_from_result(
        wf,
        {"node_1": {"x": 1}, "node_2": {"y": 2}, "node_3": {"z": 3}},
        {},
        EXEC,
        per_node,
    )

    by_node = {u["node_id"]: u for u in updates}
    assert by_node["node_1"]["status"] == "completed"
    assert json.loads(by_node["node_2"]["result"]) == {"y": 2}
    assert by_node["node_3"]["status"] == "completed"


def test_terminal_result_backfills_missing_error_frames():
    wf = {
        "node_1": {"node_name": "__start__", "node_type": "StartNode"},
        "node_2": {"node_name": "parse", "node_type": "CodeNode"},
    }

    updates = _terminal_node_updates_from_result(
        wf,
        {"node_1": {"x": 1}},
        {"node_2": {"error_message": "bad parse"}},
        EXEC,
        {"node_1": {"status": "completed"}},
    )

    assert updates == [{
        "exec_id": EXEC,
        "node_id": "node_2",
        "node_name": "parse",
        "node_type": "CodeNode",
        "status": "error",
        "error": "bad parse",
    }]


def test_terminal_result_does_not_backfill_by_node_name():
    wf = {
        "node_1": {"node_name": "__start__", "node_type": "StartNode"},
        "node_2": {"node_name": "__end__", "node_type": "EndNode"},
        "node_3": {"node_name": "__end__", "node_type": "EndNode"},
    }

    updates = _terminal_node_updates_from_result(
        wf,
        {"__start__": {"x": 1}, "__end__": {"z": 3}},
        {"__end__": {"error_message": "bad end"}},
        EXEC,
        {},
    )

    assert updates == []
