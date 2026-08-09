"""RE-6 Warm T1 — pure-engine ``sandbox_entry serve`` loop.

The warm worker imports the engine ONCE then serves many runs over a
file-based job channel on the bind-mounted work dir. These tests exercise the
PURE-engine ``serve_once`` claim/run/done cycle (no api):

- a ready job is claimed, run, and its ``.done`` written;
- a crashing job still writes ``.done`` and never raises (loop survives);
- no ready job → ``serve_once`` returns ``None``.
"""

from __future__ import annotations

import json
import os

from vibecanvas_engine import sandbox_entry


def _min_code_wf() -> dict:
    """Minimal Start -> Code -> End workflow (mirrors test_sandbox_entry.py).

    The CodeNode returns ``{"v": inputs["x"] + 1}``; End references it so the
    final_outputs carry the computed value.
    """
    return {
        "__meta__": {
            "workflow_id": "wf_sandbox_serve",
            "workflow_name": "sandbox_serve_smoke",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {},
            "output_fields": {
                "x": {"type": "integer", "description": "input number"},
            },
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "compute",
            "node_type": "CodeNode",
            "node_description": "increment x",
            "input_fields": {
                "x": {"type": "integer", "value": 0, "reference": "__start__.x"},
            },
            "output_fields": {
                "v": {"type": "integer", "description": "x + 1"},
            },
            "node_config": {
                "programming_language": "python",
                "process_fn": "def process_fn(inputs):\n    return {'v': inputs['x'] + 1}",
            },
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "v": {"type": "integer", "value": 0, "reference": "compute.v"},
            },
            "output_fields": {
                "v": {"type": "integer", "description": "x + 1"},
            },
            "node_config": {},
            "children": [],
        },
    }


def _job(work, runs, job_id, tenant, run_id, wf, inputs):
    rd = os.path.join(runs, tenant, run_id, "__exec__")
    os.makedirs(rd, exist_ok=True)
    open(os.path.join(rd, "workflow.json"), "w").write(json.dumps(wf))
    open(os.path.join(rd, "inputs.json"), "w").write(json.dumps(inputs))
    inbox = os.path.join(work, "inbox")
    os.makedirs(inbox, exist_ok=True)
    open(os.path.join(inbox, f"{job_id}.json"), "w").write(
        json.dumps({"job_id": job_id, "tenant": tenant, "run_id": run_id})
    )
    # atomic-ish marker (test); production writes write-then-rename.
    open(os.path.join(inbox, f"{job_id}.ready"), "w").write("")


def test_serve_once_runs_job_and_writes_done(tmp_path):
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    os.makedirs(work)
    os.makedirs(runs)
    _job(work, runs, "j1", "t", "r1", _min_code_wf(), {"x": 1})

    claimed = sandbox_entry.serve_once(work, runs)

    assert claimed == "j1"
    assert os.path.exists(os.path.join(work, "outbox", "j1.done"))
    res = json.loads(
        open(os.path.join(runs, "t", "r1", "__exec__", "result.json")).read()
    )
    assert res["error_dict"] == {} and res["final_outputs"]
    # inbox markers cleaned.
    assert not os.path.exists(os.path.join(work, "inbox", "j1.taken"))
    assert not os.path.exists(os.path.join(work, "inbox", "j1.json"))


def test_serve_once_crashing_job_writes_done_not_raises(tmp_path):
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    os.makedirs(work)
    os.makedirs(runs)
    _job(work, runs, "j2", "t", "r2", {"__meta__": {}, "n": {"node_type": "NopeNode"}}, {})

    # bad wf → caught, .done written, no raise.
    claimed = sandbox_entry.serve_once(work, runs)

    assert claimed == "j2"
    assert os.path.exists(os.path.join(work, "outbox", "j2.done"))


def test_serve_once_no_job_returns_none(tmp_path):
    work = str(tmp_path / "work")
    os.makedirs(os.path.join(work, "inbox"))
    assert sandbox_entry.serve_once(work, str(tmp_path / "runs")) is None


def test_serve_once_uses_run_subpath(tmp_path):
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    os.makedirs(work)
    os.makedirs(runs)
    # per-tenant layout: run at {runs}/r1 (NO tenant prefix), job carries run_subpath="r1"
    rd = os.path.join(runs, "r1", "__exec__")
    os.makedirs(rd)
    open(os.path.join(rd, "workflow.json"), "w").write(json.dumps(_min_code_wf()))
    open(os.path.join(rd, "inputs.json"), "w").write(json.dumps({"x": 1}))
    inbox = os.path.join(work, "inbox")
    os.makedirs(inbox)
    open(os.path.join(inbox, "j1.json"), "w").write(
        json.dumps({"job_id": "j1", "tenant": "t", "run_id": "r1", "run_subpath": "r1"})
    )
    open(os.path.join(inbox, "j1.ready"), "w").write("")
    assert sandbox_entry.serve_once(work, runs) == "j1"
    res = json.loads(open(os.path.join(runs, "r1", "__exec__", "result.json")).read())
    assert res["error_dict"] == {}
    # NOT written at the legacy {tenant}/{run_id} path.
    assert not os.path.exists(os.path.join(runs, "t", "r1", "__exec__", "result.json"))


def test_serve_once_rejects_malformed_run_subpath(tmp_path):
    # A run_subpath that escapes the runs root (".." or absolute) is MALFORMED:
    # the loop must NOT escape, still write .done, survive, and never crash.
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    os.makedirs(work)
    os.makedirs(runs)
    inbox = os.path.join(work, "inbox")
    os.makedirs(inbox)
    # A real workflow tree at the legacy path so the only thing wrong is the subpath.
    rd = os.path.join(runs, "t", "r3", "__exec__")
    os.makedirs(rd)
    open(os.path.join(rd, "workflow.json"), "w").write(json.dumps(_min_code_wf()))
    open(os.path.join(rd, "inputs.json"), "w").write(json.dumps({"x": 1}))
    open(os.path.join(inbox, "j3.json"), "w").write(
        json.dumps({"job_id": "j3", "tenant": "t", "run_id": "r3", "run_subpath": "../escape"})
    )
    open(os.path.join(inbox, "j3.ready"), "w").write("")

    # No crash; .done still written so the host's poll never hangs.
    claimed = sandbox_entry.serve_once(work, runs)
    assert claimed in ("j3", None)
    assert os.path.exists(os.path.join(work, "outbox", "j3.done"))
    # Nothing escaped the runs root.
    assert not os.path.exists(os.path.join(tmp_path, "escape"))
    assert not os.path.exists(os.path.join(os.path.dirname(runs), "escape"))


def test_serve_once_rejects_absolute_run_subpath(tmp_path):
    work = str(tmp_path / "work")
    runs = str(tmp_path / "runs")
    os.makedirs(work)
    os.makedirs(runs)
    inbox = os.path.join(work, "inbox")
    os.makedirs(inbox)
    escaped = str(tmp_path / "abs" / "__exec__")
    open(os.path.join(inbox, "j4.json"), "w").write(
        json.dumps({"job_id": "j4", "tenant": "t", "run_id": "r4", "run_subpath": "/abs"})
    )
    open(os.path.join(inbox, "j4.ready"), "w").write("")

    claimed = sandbox_entry.serve_once(work, runs)
    assert claimed in ("j4", None)
    assert os.path.exists(os.path.join(work, "outbox", "j4.done"))
    # Absolute join would escape runs → must NOT have written there.
    assert not os.path.exists(os.path.join(escaped, "result.json"))
