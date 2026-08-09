"""RE-6 Warm T2 — provider ``run_serve`` (long-lived warm worker, lifecycle-inverted).

Two layers:

1. **Unit (no gVisor):** ``build_oci_config(..., rw_binds=[(dest, source), ...])``
   emits a writable bind for EACH ``(dest, source)`` pair; the back-compat shape
   ``rw_binds=[("/run", run_dir)]`` matches the old single-``/run`` mount that
   ``run``/``run_workflow`` rely on (so the refactor is behavior-preserving).

2. **gVisor (skipif not ``_gvisor_runnable()``):** boot ONE warm worker via
   ``run_serve`` (bound to a tmp runs-root + work dir + the host sys.path
   read-only binds); drop ONE serve-format job into ``{work}/inbox`` + the
   run-tier; poll the worker's ``{work}/outbox/{job}.done`` (and assert the
   ``result.json`` it produced is correct) — all while the SAME ``handle.proc``
   stays alive (one boot served the job — no per-run re-boot). Then
   ``stop_serve`` kills it + removes the bundle.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from vibecanvas_api.services.sandbox import _gvisor_runnable, build_oci_config
from vibecanvas_api.services.sandbox.gvisor import (
    RootlessGvisorProvider,
    ServeHandle,
    _workflow_python_binds,
    _workflow_python_env,
)


# ---------------------------------------------------------------------------
# a pure Start -> Code -> End workflow (mirror P2's _trivial_pure_wf)
# ---------------------------------------------------------------------------
def _min_code_wf() -> dict:
    return {
        "__meta__": {
            "workflow_id": "wf_run_serve",
            "workflow_name": "run_serve_smoke",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {},
            "output_fields": {"x": {"type": "integer", "description": "n"}},
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
            "output_fields": {"v": {"type": "integer", "description": "x + 1"}},
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
            "input_fields": {"v": {"type": "integer", "value": 0, "reference": "compute.v"}},
            "output_fields": {"v": {"type": "integer", "description": "x + 1"}},
            "node_config": {},
            "children": [],
        },
    }


# ===========================================================================
# Unit — no gVisor
# ===========================================================================
def test_build_oci_config_emits_multiple_rw_binds(tmp_path):
    """rw_binds=[("/runs", a), ("/work", b)] → both writable bind mounts present."""
    a = tmp_path / "runs"
    b = tmp_path / "work"
    a.mkdir()
    b.mkdir()
    cfg = build_oci_config(
        command=["true"],
        env=None,
        rw_binds=[("/runs", str(a)), ("/work", str(b))],
    )
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    assert mounts["/runs"]["type"] == "bind"
    assert mounts["/runs"]["source"] == str(a)
    assert "rw" in mounts["/runs"]["options"]
    assert mounts["/work"]["type"] == "bind"
    assert mounts["/work"]["source"] == str(b)
    assert "rw" in mounts["/work"]["options"]


def test_build_oci_config_run_bind_backcompat(tmp_path):
    """Back-compat: rw_binds=[("/run", run_dir)] reproduces the old single-/run
    shape that run/run_workflow + the P1 unit test depend on."""
    run_dir = str(tmp_path / "rundir")
    os.makedirs(run_dir)
    cfg = build_oci_config(
        command=["sh", "-c", "echo hi"], env={"A": "1"}, rw_binds=[("/run", run_dir)]
    )
    mounts = {m["destination"]: m for m in cfg["mounts"]}
    assert mounts["/run"]["type"] == "bind" and mounts["/run"]["source"] == run_dir
    assert "rw" in mounts["/run"]["options"]
    assert cfg["process"]["cwd"] == "/run"
    # host /run is NOT a source (B2)
    assert all(m["source"] != "/run" for m in cfg["mounts"])


# ===========================================================================
# gVisor — real runsc
# ===========================================================================
gvisor = pytest.mark.skipif(
    not _gvisor_runnable(), reason="rootless gVisor not runnable here"
)


def _drop_job(work: str, runs: str, job_id: str, tenant: str, run_id: str, wf: dict, inputs: dict) -> None:
    """Drop ONE serve-format job: run-tier __exec__ files + inbox json + .ready."""
    rd = os.path.join(runs, tenant, run_id, "__exec__")
    os.makedirs(rd, exist_ok=True)
    with open(os.path.join(rd, "workflow.json"), "w", encoding="utf-8") as f:
        json.dump(wf, f)
    with open(os.path.join(rd, "inputs.json"), "w", encoding="utf-8") as f:
        json.dump(inputs, f)
    inbox = os.path.join(work, "inbox")
    os.makedirs(inbox, exist_ok=True)
    with open(os.path.join(inbox, f"{job_id}.json"), "w", encoding="utf-8") as f:
        json.dump({"tenant": tenant, "run_id": run_id}, f)
    # atomic .ready LAST (write-then-rename).
    ready = os.path.join(inbox, f"{job_id}.ready")
    with open(ready + ".tmp", "w", encoding="utf-8") as f:
        f.write("")
    os.rename(ready + ".tmp", ready)


@gvisor
def test_run_serve_one_boot_serves_job_then_stop(tmp_path):
    """ONE warm worker boots via run_serve, serves a job over the file channel
    WITHOUT a re-boot (proc.poll None throughout), then stop_serve kills it."""
    runs = str(tmp_path / "runs")
    work = str(tmp_path / "work")
    os.makedirs(runs)
    os.makedirs(work)

    handle = RootlessGvisorProvider(_resolve()).run_serve(
        runs_root=runs,
        work_dir=work,
        ro_binds=_workflow_python_binds(),
        env=_workflow_python_env(),
    )
    try:
        assert isinstance(handle, ServeHandle)
        # The worker is alive (long-lived, not communicated/torn down).
        assert handle.proc.poll() is None, "worker did not stay alive after run_serve"

        _drop_job(work, runs, "j1", "t", "r1", _min_code_wf(), {"x": 1})

        done = os.path.join(work, "outbox", "j1.done")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if os.path.exists(done):
                break
            # if the worker died, fail fast with its diagnosis
            assert handle.proc.poll() is None, "worker DIED while serving the job"
            time.sleep(0.05)

        assert os.path.exists(done), "outbox .done never appeared (job not served)"
        # ONE boot served it — still the SAME live process (no per-run re-boot).
        assert handle.proc.poll() is None, "worker re-booted / died after serving"

        result_path = os.path.join(runs, "t", "r1", "__exec__", "result.json")
        assert os.path.exists(result_path), "worker did not write result.json"
        res = json.loads(open(result_path, encoding="utf-8").read())
        assert res["error_dict"] == {}, f"engine errors: {res['error_dict']}"
        assert res["final_outputs"].get("__end__", {}).get("v") == 2
    finally:
        RootlessGvisorProvider(_resolve()).stop_serve(handle)

    # killed + bundle cleaned.
    handle.proc.wait(timeout=10)
    assert handle.proc.poll() is not None, "worker not killed by stop_serve"
    assert not os.path.exists(handle.bundle_dir), "bundle dir not removed by stop_serve"


def _resolve() -> str:
    from vibecanvas_api.services.sandbox import _resolve_runsc

    return _resolve_runsc()
