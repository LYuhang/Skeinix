import json
import os
from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider


def _trivial_wf():
    meta = {"workflow_id": "w", "workflow_name": "w", "workflow_version": 1, "workflow_subversion": 0}
    return {"__meta__": meta,
            "node_1": {"node_id": "node_1", "node_name": "__start__", "node_type": "StartNode",
                       "node_description": "s", "input_fields": {}, "output_fields": {},
                       "node_config": {}, "children": []}}


def test_build_invocation_writes_job_json_kind_workflow(tmp_path):
    prov = RootlessGvisorProvider("/nonexistent/runsc")
    prov._build_workflow_invocation(
        run_dir=str(tmp_path), workflow=_trivial_wf(), inputs={}, run_id="r1",
        tenant=None,
    )
    job_path = tmp_path / "__exec__" / "job.json"
    assert job_path.exists()
    assert json.loads(job_path.read_text())["kind"] == "workflow"


def test_cold_boot_provider_is_the_gvisor_provider():
    from vibecanvas_api.services.sandbox import ColdBootProvider
    from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider
    assert issubclass(ColdBootProvider, RootlessGvisorProvider) or ColdBootProvider is RootlessGvisorProvider
    prov = ColdBootProvider("/nonexistent/runsc")
    assert hasattr(prov, "launch_workflow_bus") and hasattr(prov, "run_workflow")


def test_run_node_writes_node_job_and_reads_result(tmp_path, monkeypatch):
    from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider
    from vibecanvas_api.services.sandbox.provider import SandboxResult
    prov = RootlessGvisorProvider("/nonexistent/runsc")
    node = {"node_id": "node_7", "node_name": "c", "node_type": "CodeNode",
            "node_description": "d", "input_fields": {}, "output_fields": {},
            "node_config": {"programming_language": "python",
                            "process_fn": "def process_fn(inputs):\n    return {'v': 1}"},
            "children": []}

    def fake_run(*, run_dir, command, env=None, network="host", timeout=60.0,
                 extra_ro_binds=(), data_dir=None, bus_socket=None):
        exec_dir = os.path.join(run_dir, "__exec__")
        job = json.loads(open(os.path.join(exec_dir, "job.json")).read())
        assert job["kind"] == "node" and job["node"]["node_id"] == "node_7"
        with open(os.path.join(exec_dir, "result.json"), "w") as f:
            json.dump({"final_outputs": {"node_7": {"v": 1}}, "error_dict": {}, "execution_time": 0.0}, f)
        with open(os.path.join(exec_dir, "events.ndjson"), "w") as f:
            f.write("")
        return SandboxResult(exit_code=0, stdout="", stderr="", duration_s=0.0)

    monkeypatch.setattr(prov, "run", fake_run)
    res = prov.run_node(run_dir=str(tmp_path), node=node, inputs={}, run_id="rn")
    assert res.final_outputs["node_7"]["v"] == 1
    assert res.error_dict == {}
