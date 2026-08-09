# -*- coding: utf-8 -*-
"""Tests for the LEAN per-run CodeNode worker pool + stdlib shim.

These are sync (subprocess/thread) tests — no asyncio. The pool spawns real
``python -m vibecanvas_engine.code_worker`` subprocesses.
"""

import os
import sys
import threading

import pytest

from vibecanvas_engine.code_runner import CodeWorkerPool

PYTHONPATH = os.pathsep.join(sys.path)


@pytest.fixture
def pool(tmp_path):
    p = CodeWorkerPool(pythonpath=PYTHONPATH, cwd=str(tmp_path), max_workers=4)
    yield p
    p.close()


def test_runs_and_returns_dict(pool):
    code = "def process_fn(inputs):\n    return {'v': inputs['x'] + 1}"
    res = pool.run(code, {"x": 5}, timeout=10)
    assert res["status"] == "success"
    assert res["output"] == {"v": 6}


def test_normal_import_works(pool):
    code = "import math\ndef process_fn(inputs):\n    return {'v': math.sqrt(inputs['x'])}"
    res = pool.run(code, {"x": 9}, timeout=10)
    assert res["status"] == "success"
    assert res["output"] == {"v": 3.0}


def test_timeout_kills_and_respawns(pool):
    spin = "def process_fn(inputs):\n    while True:\n        pass"
    res = pool.run(spin, {}, timeout=1)
    assert res["status"] == "error"
    assert "timed out" in res["error_message"]

    # A subsequent run must succeed (pool respawned a fresh worker).
    ok = "def process_fn(inputs):\n    return {'ok': True}"
    res2 = pool.run(ok, {}, timeout=10)
    assert res2["status"] == "success"
    assert res2["output"] == {"ok": True}


def test_user_print_does_not_corrupt(pool):
    code = (
        "def process_fn(inputs):\n"
        "    print('hello stdout')\n"
        "    import sys\n"
        "    sys.stderr.write('hello stderr\\n')\n"
        "    return {'v': inputs['x'] * 2}"
    )
    res = pool.run(code, {"x": 21}, timeout=10)
    assert res["status"] == "success"
    assert res["output"] == {"v": 42}


def test_non_json_output_errors(pool):
    # non-serializable value inside a dict
    code = "def process_fn(inputs):\n    return {'x': object()}"
    res = pool.run(code, {}, timeout=10)
    assert res["status"] == "error"
    assert "JSON-serializable" in res["error_message"]

    # non-dict return
    code2 = "def process_fn(inputs):\n    return [1, 2, 3]"
    res2 = pool.run(code2, {}, timeout=10)
    assert res2["status"] == "error"
    assert "JSON-serializable dict" in res2["error_message"]


def test_worker_crash_detected(pool):
    code = "def process_fn(inputs):\n    import os\n    os._exit(7)"
    res = pool.run(code, {}, timeout=10)
    assert res["status"] == "error"
    assert "crashed" in res["error_message"]

    # subsequent run works (respawn)
    ok = "def process_fn(inputs):\n    return {'ok': 1}"
    res2 = pool.run(ok, {}, timeout=10)
    assert res2["status"] == "success"
    assert res2["output"] == {"ok": 1}


def test_concurrent_runs_no_interleave(pool):
    code = (
        "def process_fn(inputs):\n"
        "    import time\n"
        "    time.sleep(0.3)\n"
        "    return {'echo': inputs['tag']}"
    )
    results = {}

    def worker(tag):
        results[tag] = pool.run(code, {"tag": tag}, timeout=10)

    t1 = threading.Thread(target=worker, args=("A",))
    t2 = threading.Thread(target=worker, args=("B",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert results["A"]["status"] == "success"
    assert results["A"]["output"] == {"echo": "A"}
    assert results["B"]["status"] == "success"
    assert results["B"]["output"] == {"echo": "B"}


def test_env_scrubbed(monkeypatch, tmp_path):
    # SECRETS are scrubbed even when the PARENT has them set (C1) — but the
    # egress-proxy control vars (HTTP(S)_PROXY/NO_PROXY) ARE forwarded, because
    # they are how a CodeNode's outbound HTTP stays gated through the broker
    # allowlist (proxy mode). They're egress control, not credentials.
    monkeypatch.setenv("DATABASE_URL", "postgres://secret:secret@host/db")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-super-secret-not-a-real-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://egress:3128")

    p = CodeWorkerPool(pythonpath=PYTHONPATH, cwd=str(tmp_path), max_workers=2)
    try:
        code = (
            "def process_fn(inputs):\n"
            "    import os\n"
            "    return {\n"
            "        'db': os.environ.get('DATABASE_URL'),\n"
            "        'key': os.environ.get('OPENAI_API_KEY'),\n"
            "        'proxy': os.environ.get('HTTPS_PROXY'),\n"
            "    }"
        )
        res = p.run(code, {}, timeout=10)
        assert res["status"] == "success"
        assert res["output"]["db"] is None
        assert res["output"]["key"] is None
        # egress control var forwarded so the in-sandbox proxy can gate egress
        assert res["output"]["proxy"] == "http://egress:3128"
    finally:
        p.close()
