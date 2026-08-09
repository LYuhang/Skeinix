import json, os
from unittest.mock import patch, MagicMock
from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider
from vibecanvas_api.config import config

def _fake_run_factory(seen):
    def fake_run(run_dir, command, env=None, network=None, timeout=None, extra_ro_binds=None, data_dir=None, extra_rw_binds=None, bus_socket=None, **kw):
        seen["network"] = network
        seen["extra_rw_binds"] = extra_rw_binds
        json.dump({"final_outputs":{"stdout":"","stderr":"","exit_code":0},"error_dict":{},"execution_time":0.0},
                  open(os.path.join(run_dir,"__exec__","result.json"),"w"))
        m = MagicMock(); m.exit_code = 0; return m
    return fake_run

def test_run_code_default_requests_configured_egress(tmp_path):
    rd=str(tmp_path); os.makedirs(os.path.join(rd,"__exec__"),exist_ok=True)
    seen={}; p=RootlessGvisorProvider("/nonexistent/runsc")
    with patch.object(p,"run",side_effect=_fake_run_factory(seen)):
        p.run_code(run_dir=rd, script="x", inputs={}, run_id="r", timeout=5)
    assert seen["network"]=="host"

def test_run_code_explicit_offline_and_rw_binds(tmp_path):
    rd=str(tmp_path); os.makedirs(os.path.join(rd,"__exec__"),exist_ok=True)
    seen={}; p=RootlessGvisorProvider("/nonexistent/runsc")
    with patch.object(p,"run",side_effect=_fake_run_factory(seen)):
        p.run_code(run_dir=rd, script="x", inputs={}, run_id="r", timeout=5,
                   network="none", extra_rw_binds=[("/opt/agent-overlay","/host/ov")])
    assert seen["network"]=="none"
    assert ("/opt/agent-overlay","/host/ov") in (seen["extra_rw_binds"] or [])


def test_run_code_host_request_uses_proxy_in_proxy_mode(tmp_path, monkeypatch):
    rd = str(tmp_path)
    os.makedirs(os.path.join(rd, "__exec__"), exist_ok=True)
    seen = {}
    provider = RootlessGvisorProvider("/nonexistent/runsc")

    class Loop:
        stopped = False

        def stop(self):
            self.stopped = True

    loop = Loop()
    monkeypatch.setattr(config, "sandbox_egress_mode", "proxy")
    monkeypatch.setattr(
        provider,
        "_sandbox_egress_setup",
        lambda _run_id, _allow_hosts: (
            loop,
            str(tmp_path / "egress" / "egress.sock"),
            {"HTTP_PROXY": "http://127.0.0.1:13128"},
        ),
    )
    with patch.object(provider, "run", side_effect=_fake_run_factory(seen)):
        provider.run_code(
            run_dir=rd,
            script="x",
            inputs={},
            run_id="r",
            timeout=5,
            network="host",
        )
    assert seen["network"] == "none"
    assert loop.stopped is True
