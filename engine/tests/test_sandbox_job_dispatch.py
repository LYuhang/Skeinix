import json, os
from vibecanvas_engine.sandbox_entry import run_job, read_job, JOB_WORKFLOW, JOB_UNSUPPORTED_KINDS


def _meta():
    return {"workflow_id": "w", "workflow_name": "w", "workflow_version": 1, "workflow_subversion": 0}


def _trivial_wf():
    return {
        "__meta__": _meta(),
        "node_1": {"node_id": "node_1", "node_name": "__start__", "node_type": "StartNode",
                   "node_description": "s", "input_fields": {},
                   "output_fields": {"x": {"type": "integer", "description": "n"}},
                   "node_config": {}, "children": ["node_2"]},
        "node_2": {"node_id": "node_2", "node_name": "c", "node_type": "CodeNode",
                   "node_description": "inc",
                   "input_fields": {"x": {"type": "integer", "value": 0, "reference": "__start__.x"}},
                   "output_fields": {"v": {"type": "integer", "description": "x+1"}},
                   "node_config": {"programming_language": "python",
                                   "process_fn": "def process_fn(inputs):\n    return {'v': inputs['x']+1}"},
                   "children": ["node_3"]},
        "node_3": {"node_id": "node_3", "node_name": "__end__", "node_type": "EndNode",
                   "node_description": "e",
                   "input_fields": {"v": {"type": "integer", "value": 0, "reference": "c.v"}},
                   "output_fields": {"v": {"type": "integer", "description": "x+1"}},
                   "node_config": {}, "children": []},
    }


def _seed(run_root, *, job=None):
    exec_dir = os.path.join(run_root, "__exec__")
    os.makedirs(exec_dir, exist_ok=True)
    with open(os.path.join(exec_dir, "workflow.json"), "w") as f:
        json.dump(_trivial_wf(), f)
    with open(os.path.join(exec_dir, "inputs.json"), "w") as f:
        json.dump({"x": 1}, f)
    if job is not None:
        with open(os.path.join(exec_dir, "job.json"), "w") as f:
            json.dump(job, f)


def _result(run_root):
    with open(os.path.join(run_root, "__exec__", "result.json")) as f:
        return json.load(f)


def test_read_job_defaults_to_workflow_when_absent(tmp_path):
    os.makedirs(tmp_path / "__exec__")
    assert read_job(str(tmp_path))["kind"] == JOB_WORKFLOW


def test_run_job_workflow_kind_runs_the_graph(tmp_path):
    _seed(str(tmp_path), job={"kind": JOB_WORKFLOW})
    rc = run_job(str(tmp_path), "r1")
    assert rc == 0
    assert _result(str(tmp_path))["final_outputs"]["__end__"]["v"] == 2


def test_run_job_no_descriptor_is_back_compat_workflow(tmp_path):
    _seed(str(tmp_path))  # no job.json
    rc = run_job(str(tmp_path), "r1")
    assert rc == 0
    assert _result(str(tmp_path))["final_outputs"]["__end__"]["v"] == 2


def test_run_job_unsupported_kind_writes_clean_error_result(tmp_path):
    _seed(str(tmp_path), job={"kind": "tool"})
    rc = run_job(str(tmp_path), "r1")
    assert rc == 2
    res = _result(str(tmp_path))
    assert res["final_outputs"] == {}
    assert "tool" in res["error_dict"]["__engine__"]
    assert "tool" in JOB_UNSUPPORTED_KINDS and "code" not in JOB_UNSUPPORTED_KINDS
