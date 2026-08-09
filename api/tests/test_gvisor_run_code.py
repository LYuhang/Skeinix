import json, os
from unittest.mock import patch, MagicMock
from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider


def test_run_code_writes_job_and_parses_result(tmp_path):
    run_dir = str(tmp_path)
    os.makedirs(os.path.join(run_dir, "__exec__"), exist_ok=True)
    captured = {}

    def fake_run(run_dir, command, env=None, network=None, timeout=None,
                 extra_ro_binds=None, data_dir=None, bus_socket=None, **kw):
        captured["network"] = network
        captured["job"] = json.load(open(os.path.join(run_dir, "__exec__", "job.json")))
        with open(os.path.join(run_dir, "__exec__", "result.json"), "w") as f:
            json.dump({"final_outputs": {"stdout": "ok", "stderr": "", "exit_code": 0},
                       "error_dict": {}, "execution_time": 0.1}, f)
        m = MagicMock(); m.exit_code = 0; m.stdout = ""; m.stderr = ""
        return m

    p = RootlessGvisorProvider("/nonexistent/runsc")
    with patch.object(p, "run", side_effect=fake_run):
        res = p.run_code(run_dir=run_dir, script="print(1)", inputs={"x": 1},
                         run_id="r1", timeout=30)
    # The default code job asks for egress. In the development host-network
    # profile that resolves to hostinet; production proxy mode is separately
    # covered as network-none plus the controlled egress broker.
    assert captured["network"] == "host"
    assert (captured["job"]["kind"] == "code"
            and captured["job"]["script"] == "print(1)"
            and captured["job"]["inputs"] == {"x": 1})
    assert res.final_outputs["stdout"] == "ok"


def test_run_code_can_hide_run_mount_for_chat(tmp_path):
    run_dir = str(tmp_path)
    captured = {}

    def fake_run(run_dir, command, env=None, network=None, timeout=None,
                 extra_ro_binds=None, data_dir=None, run_mount="/run", **kw):
        captured["run_dir"] = run_dir
        captured["run_mount"] = run_mount
        captured["run_root"] = (env or {}).get("VIBECANVAS_RUN_ROOT")
        captured["job_exists_at_run"] = os.path.exists(
            os.path.join(run_dir, "__exec__", "job.json")
        )
        with open(os.path.join(run_dir, "__exec__", "result.json"), "w") as f:
            json.dump({"final_outputs": {"stdout": "ok", "stderr": "", "exit_code": 0},
                       "error_dict": {}, "execution_time": 0.1}, f)
        m = MagicMock(); m.exit_code = 0; m.stdout = ""; m.stderr = ""
        return m

    p = RootlessGvisorProvider("/nonexistent/runsc")
    with patch.object(p, "run", side_effect=fake_run):
        p.run_code(run_dir=run_dir, script="print(1)", inputs={},
                   run_id="r1", timeout=30, expose_run=False)
    assert captured["run_mount"] == "/tmp"
    assert captured["run_root"] == "/tmp"
    assert captured["run_dir"] == os.path.join(run_dir, "__exec_channel")
    assert captured["job_exists_at_run"] is True
    assert not os.path.exists(os.path.join(run_dir, "__exec_channel", "data"))
