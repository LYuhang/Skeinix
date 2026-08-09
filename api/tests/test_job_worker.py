# -*- coding: utf-8 -*-
"""job_worker — in-sandbox per-job execution unit for the parallel serve loop."""


def test_run_job_workflow_delegates_to_run_one(monkeypatch):
    import vibecanvas_api.sandbox_entry as se
    from vibecanvas_api.services.sandbox import job_worker

    called = {}
    monkeypatch.setattr(
        se, "run_one",
        lambda rr, rid, t: called.update(run_root=rr, run_id=rid, tenant=t) or 0,
    )
    res = job_worker.run_job(
        {"kind": "workflow", "run_root": "/runs/t/r1", "run_id": "r1", "tenant": "t"}
    )
    assert called == {"run_root": "/runs/t/r1", "run_id": "r1", "tenant": "t"}
    assert res["status"] == "success"
    assert res["exit_code"] == 0


def test_run_job_node_delegates_to_unified_run_one(monkeypatch):
    import vibecanvas_api.sandbox_entry as se
    from vibecanvas_api.services.sandbox import job_worker

    called = {}
    monkeypatch.setattr(
        se, "run_one",
        lambda rr, rid, t: called.update(run_root=rr, run_id=rid, tenant=t) or 0,
    )
    res = job_worker.run_job(
        {"kind": "node", "run_root": "/runs/t/r1", "run_id": "r1", "tenant": "t"}
    )
    assert called == {"run_root": "/runs/t/r1", "run_id": "r1", "tenant": "t"}
    assert res["status"] == "success"


def test_run_job_nonzero_exit_is_error(monkeypatch):
    import vibecanvas_api.sandbox_entry as se
    from vibecanvas_api.services.sandbox import job_worker

    monkeypatch.setattr(se, "run_one", lambda rr, rid, t: 1)
    res = job_worker.run_job(
        {"kind": "workflow", "run_root": "/r", "run_id": "r1", "tenant": "t"}
    )
    assert res["status"] == "error"
    assert res["exit_code"] == 1


def test_run_job_run_one_raises_is_error(monkeypatch):
    import vibecanvas_api.sandbox_entry as se
    from vibecanvas_api.services.sandbox import job_worker

    def _boom(rr, rid, t):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(se, "run_one", _boom)
    res = job_worker.run_job(
        {"kind": "workflow", "run_root": "/r", "run_id": "r1", "tenant": "t"}
    )
    assert res["status"] == "error"
    assert "kaboom" in res["error_message"]


def test_run_job_malformed_descriptor_is_error():
    from vibecanvas_api.services.sandbox import job_worker

    res = job_worker.run_job({"kind": "bogus"})
    assert res["status"] == "error"
    assert "run_root" in res["error_message"]
