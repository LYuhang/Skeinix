import json, os, tempfile
from vibecanvas_engine import sandbox_entry

def _run_root(job: dict) -> str:
    root = tempfile.mkdtemp()
    ed = os.path.join(root, "__exec__"); os.makedirs(ed)
    with open(os.path.join(ed, "job.json"), "w") as f: json.dump(job, f)
    return root

def _result(root):
    with open(os.path.join(root, "__exec__", "result.json")) as f: return json.load(f)

def test_code_job_captures_stdout():
    root = _run_root({"kind": "code", "script": "import json,sys\nd=json.load(sys.stdin)\nprint('hi', d['x'])", "inputs": {"x": 5}})
    assert sandbox_entry.run_job(root, "r1") == 0
    out = _result(root)["final_outputs"]
    assert "hi 5" in out["stdout"] and out["exit_code"] == 0

def test_code_job_nonzero_exit_carries_stderr():
    root = _run_root({"kind": "code", "script": "import sys; sys.stderr.write('boom'); sys.exit(3)", "inputs": {}})
    sandbox_entry.run_job(root, "r1")
    res = _result(root)
    assert res["final_outputs"]["exit_code"] == 3 and "boom" in res["final_outputs"]["stderr"]
    assert res["error_dict"]

def test_code_job_timeout():
    root = _run_root({"kind": "code", "script": "import time; time.sleep(5)", "inputs": {}, "timeout_s": 1})
    sandbox_entry.run_job(root, "r1")
    assert _result(root)["final_outputs"]["exit_code"] == -1
