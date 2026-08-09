# -*- coding: utf-8 -*-
"""BoundedSubprocessPool — the generic, engine-agnostic worker pool skeleton
extracted from code_runner (A1). Pure stdlib; the worker script is pluggable so
both CodeNode (code_worker.py) and the sandbox parallel serve (job_worker.py)
reuse one pool.
"""
import os
import textwrap

from vibecanvas_engine.subprocess_pool import BoundedSubprocessPool

# A trivial echo worker (stdlib-only): read one framed job dict, reply
# {"status": "success", "output": {"echo": <job>}}.
_ECHO = textwrap.dedent('''
    import os, sys, struct, json
    _LEN = struct.Struct(">I")
    jr, rw = int(sys.argv[1]), int(sys.argv[2])
    def _read(fd, n):
        buf = b""
        while len(buf) < n:
            c = os.read(fd, n - len(buf))
            if not c: raise EOFError
            buf += c
        return buf
    while True:
        try:
            n = _LEN.unpack(_read(jr, 4))[0]
            job = json.loads(_read(jr, n))
        except Exception:
            break
        body = json.dumps({"status": "success", "output": {"echo": job}}).encode()
        os.write(rw, _LEN.pack(len(body))); os.write(rw, body)
''')


def _env():
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}


def test_pool_runs_and_returns_in_order(tmp_path):
    script = tmp_path / "echo_worker.py"
    script.write_text(_ECHO)
    pool = BoundedSubprocessPool(worker_script=str(script), cwd=str(tmp_path),
                                 env=_env(), max_workers=2)
    try:
        r1 = pool.run({"a": 1}, timeout=5)
        r2 = pool.run({"a": 2}, timeout=5)
        assert r1["status"] == "success" and r1["output"]["echo"] == {"a": 1}
        assert r2["status"] == "success" and r2["output"]["echo"] == {"a": 2}
    finally:
        pool.close()


def test_pool_reuses_idle_worker(tmp_path):
    """Two sequential runs at max_workers=1 must reuse the single worker (no spawn storm)."""
    script = tmp_path / "echo_worker.py"
    script.write_text(_ECHO)
    spawned = {"n": 0}

    class _Counting(BoundedSubprocessPool):
        def _spawn(self):
            spawned["n"] += 1
            return super()._spawn()

    pool = _Counting(worker_script=str(script), cwd=str(tmp_path), env=_env(), max_workers=1)
    try:
        pool.run({"a": 1}, timeout=5)
        pool.run({"a": 2}, timeout=5)
        assert spawned["n"] == 1  # reused, not respawned
    finally:
        pool.close()


def test_pool_timeout_kills_and_recovers(tmp_path):
    hang = tmp_path / "hang_worker.py"
    # Reads the job then hangs forever (never replies) → parent read_result times out.
    hang.write_text(textwrap.dedent('''
        import os, sys, struct, time
        _LEN = struct.Struct(">I")
        jr = int(sys.argv[1])
        n = _LEN.unpack(os.read(jr, 4))[0]; os.read(jr, n)
        while True: time.sleep(1)
    '''))
    pool = BoundedSubprocessPool(worker_script=str(hang), cwd=str(tmp_path),
                                 env=_env(), max_workers=1)
    try:
        res = pool.run({"x": 1}, timeout=0.5)
        assert res["status"] == "error" and "timed out" in res["error_message"]
    finally:
        pool.close()


def test_custom_error_messages(tmp_path):
    hang = tmp_path / "hang2.py"
    hang.write_text(textwrap.dedent('''
        import os, sys, struct, time
        _LEN = struct.Struct(">I")
        jr = int(sys.argv[1])
        n = _LEN.unpack(os.read(jr, 4))[0]; os.read(jr, n)
        while True: time.sleep(1)
    '''))
    pool = BoundedSubprocessPool(worker_script=str(hang), cwd=str(tmp_path),
                                 env=_env(), max_workers=1)
    try:
        res = pool.run({"x": 1}, timeout=0.5, timeout_msg="my custom timeout")
        assert res["error_message"] == "my custom timeout"
    finally:
        pool.close()
