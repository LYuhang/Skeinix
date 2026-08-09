import json, os
from vibecanvas_engine.sandbox_entry import run_job, JOB_UNSUPPORTED_KINDS


def _code_node():
    return {"node_id": "node_7", "node_name": "c", "node_type": "CodeNode",
            "node_description": "d",
            "input_fields": {"x": {"type": "integer", "value": 3, "reference": ""}},
            "output_fields": {"v": {"type": "integer", "description": "x+1"}},
            "node_config": {"programming_language": "python",
                            "process_fn": "def process_fn(inputs):\n    return {'v': inputs['x']+1}"},
            "children": []}


def _seed_node(run_root, node, inputs, extra=None):
    exec_dir = os.path.join(run_root, "__exec__")
    os.makedirs(exec_dir, exist_ok=True)
    with open(os.path.join(exec_dir, "job.json"), "w") as f:
        json.dump({"kind": "node", "node": node, "inputs": inputs, "extra": extra or {}}, f)


def _result(run_root):
    with open(os.path.join(run_root, "__exec__", "result.json")) as f:
        return json.load(f)


def test_node_kind_runs_single_node(tmp_path):
    _seed_node(str(tmp_path), _code_node(), {"x": 10})
    rc = run_job(str(tmp_path), "rn")
    assert rc == 0
    res = _result(str(tmp_path))
    assert res["final_outputs"]["node_7"]["v"] == 11
    assert res["error_dict"] == {}


def test_node_kind_propagates_node_error(tmp_path):
    bad = _code_node()
    # NOTE: RuntimeError is NOT in the CodeNode sandbox builtin whitelist
    # (only Exception/ValueError are) — use Exception so 'boom' surfaces.
    bad["node_config"]["process_fn"] = "def process_fn(inputs):\n    raise Exception('boom')"
    _seed_node(str(tmp_path), bad, {})
    run_job(str(tmp_path), "rn")
    res = _result(str(tmp_path))
    assert res["error_dict"].get("node_7")
    assert "boom" in res["error_dict"]["node_7"]


def test_node_removed_from_unsupported(tmp_path):
    assert "node" not in JOB_UNSUPPORTED_KINDS
    assert "tool" in JOB_UNSUPPORTED_KINDS and "code" not in JOB_UNSUPPORTED_KINDS


from vibecanvas_engine.sandbox_entry import _read_host_extra


def test_read_host_extra_present(tmp_path):
    exec_dir = tmp_path / "__exec__"
    exec_dir.mkdir()
    (exec_dir / "extra.json").write_text(
        json.dumps({"llm_credentials": {"Seed2.0": {"api_key": "k", "api_url": "u"}}})
    )
    out = _read_host_extra(str(exec_dir))
    assert out["llm_credentials"]["Seed2.0"]["api_key"] == "k"


def test_read_host_extra_absent_or_malformed(tmp_path):
    exec_dir = tmp_path / "__exec__"
    exec_dir.mkdir()
    assert _read_host_extra(str(exec_dir)) == {}          # absent
    (exec_dir / "extra.json").write_text("{ not json")
    assert _read_host_extra(str(exec_dir)) == {}          # malformed → {}
