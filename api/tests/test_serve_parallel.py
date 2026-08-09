# -*- coding: utf-8 -*-
"""serve_loop_parallel — in-sandbox parallel serve over the file job channel (A3).

Mirrors serve_loop_api but consumes via a BoundedSubprocessPool(concurrency)
instead of running one job inline. Claim = atomic rename .ready→.taken; each
claimed job runs on a worker, then writes outbox/{id}.result.json (atomic) +
.done. Dispatch on job.kind; isolated per job by run_subpath.
"""
import json
import threading
import time
from pathlib import Path


def _put_job(inbox: Path, job_id: str, descriptor: dict):
    # Host writes the descriptor, THEN the .ready marker last (atomic claim gate).
    (inbox / f"{job_id}.json").write_text(json.dumps(descriptor))
    (inbox / f"{job_id}.ready").write_text("")


def test_serve_parallel_processes_all(tmp_path, monkeypatch):
    from vibecanvas_api import sandbox_entry as se

    work = tmp_path / "work"
    (work / "inbox").mkdir(parents=True)
    (work / "outbox").mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()

    processed = []
    lock = threading.Lock()

    class _FakePool:
        def __init__(self, **kw):
            pass

        def run(self, job, timeout):
            # The serve loop must have resolved run_subpath → run_root.
            assert job["run_root"].startswith(str(runs))
            with lock:
                processed.append(job["run_id"])
            return {"status": "success", "run_id": job["run_id"]}

        def close(self):
            pass

    monkeypatch.setattr(se, "_build_job_pool",
                        lambda concurrency, runs_root: _FakePool())

    for i in range(3):
        _put_job(work / "inbox", f"j{i}",
                 {"kind": "workflow", "run_id": f"r{i}", "tenant": "t",
                  "run_subpath": f"b/{i}"})

    t = threading.Thread(target=se.serve_loop_parallel,
                         args=(str(work), str(runs), 2), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and len(list((work / "outbox").glob("*.done"))) < 3:
        time.sleep(0.05)
    (work / "shutdown").write_text("")
    t.join(timeout=3)

    assert sorted(processed) == ["r0", "r1", "r2"]
    assert len(list((work / "outbox").glob("*.done"))) == 3
    for i in range(3):
        res = json.loads((work / "outbox" / f"j{i}.result.json").read_text())
        assert res["status"] == "success" and res["run_id"] == f"r{i}"
    # inbox markers cleaned up
    assert not list((work / "inbox").glob("*.taken"))
    assert not list((work / "inbox").glob("*.json"))


def test_claim_is_exclusive(tmp_path):
    """Concurrent claims of the same job — rename atomicity → exactly one winner."""
    from vibecanvas_api import sandbox_entry as se

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "j.ready").write_text("")

    results = []
    lock = threading.Lock()

    def _try():
        r = se._claim(str(inbox), str(inbox / "j.ready"))
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_try) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(1 for r in results if r == "j") == 1
    assert results.count(None) == 7


def test_activity_service_publishes_positive_busy_and_idle_state(tmp_path, monkeypatch):
    """The in-sandbox observer publishes facts, never a TTL countdown."""
    from vibecanvas_api import sandbox_entry as se

    work = tmp_path / "work"
    (work / "inbox").mkdir(parents=True)
    (work / "outbox").mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()
    entered = threading.Event()
    release = threading.Event()

    class _BlockingPool:
        def run(self, job, timeout):
            entered.set()
            assert release.wait(3)
            return {"ok": True}

        def close(self):
            pass

    monkeypatch.setattr(se, "_build_job_pool", lambda concurrency, runs_root: _BlockingPool())
    thread = threading.Thread(
        target=se.serve_loop_parallel,
        args=(str(work), str(runs), 1),
        daemon=True,
    )
    thread.start()
    _put_job(
        work / "inbox",
        "observed",
        {"kind": "workflow", "run_id": "run", "tenant": "tenant"},
    )
    assert entered.wait(3)

    busy = json.loads((work / "activity.json").read_text())
    assert busy["active_jobs"] == 1
    assert busy["idle_since_monotonic_ns"] is None
    assert "ttl" not in busy

    release.set()
    deadline = time.time() + 3
    while time.time() < deadline:
        idle = json.loads((work / "activity.json").read_text())
        if idle["active_jobs"] == 0:
            break
        time.sleep(0.02)
    assert idle["active_jobs"] == 0
    assert idle["sequence"] == 2
    assert isinstance(idle["idle_since_monotonic_ns"], int)

    (work / "shutdown").write_text("")
    thread.join(timeout=3)


def test_malformed_run_subpath_is_rejected(tmp_path, monkeypatch):
    """A descriptor whose run_subpath escapes the runs root → error result, never run."""
    from vibecanvas_api import sandbox_entry as se

    work = tmp_path / "work"
    (work / "inbox").mkdir(parents=True)
    (work / "outbox").mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()

    ran = []

    class _FakePool:
        def __init__(self, **kw):
            pass

        def run(self, job, timeout):
            ran.append(job["run_id"])
            return {"status": "success"}

        def close(self):
            pass

    monkeypatch.setattr(se, "_build_job_pool", lambda concurrency, runs_root: _FakePool())
    _put_job(work / "inbox", "bad",
             {"kind": "workflow", "run_id": "r", "tenant": "t", "run_subpath": "../escape"})

    t = threading.Thread(target=se.serve_loop_parallel, args=(str(work), str(runs), 1), daemon=True)
    t.start()
    deadline = time.time() + 5
    while time.time() < deadline and not list((work / "outbox").glob("*.done")):
        time.sleep(0.05)
    (work / "shutdown").write_text("")
    t.join(timeout=3)

    assert ran == []  # never executed
    res = json.loads((work / "outbox" / "bad.result.json").read_text())
    assert res["ok"] is False
    assert "run_subpath" in res["error"] or "../escape" in res["error"]
