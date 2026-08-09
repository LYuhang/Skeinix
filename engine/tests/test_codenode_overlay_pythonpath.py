# -*- coding: utf-8 -*-
"""CodeNode sees stdlib + Workflow overlay + platform base dependencies.

The worker imports declared 3rd-party libs from the content-addressed OVERLAY
(bound RO by the provider, its in-sandbox path exported as ``VC_LIB_OVERLAY``) +
stdlib — and NEVER from the host site-packages. These tests pin:

  * custom Workflow packages precede explicitly mounted base packages;
  * process-start ``PYTHONPATH`` stays empty so neither tier shadows stdlib;
  * host packages are not inherited unless the sandbox explicitly exposes them
    through ``VC_SANDBOX_PYTHON_PATHS``.
"""

from __future__ import annotations

import pytest

from vibecanvas_engine.nodes.code import CodeNode


def _node(process_fn: str, timeout: float | None = None) -> CodeNode:
    n = CodeNode.__new__(CodeNode)
    n._default_timeout = 60.0
    cfg = {"programming_language": "python", "process_fn": process_fn}
    if timeout is not None:
        cfg["timeout"] = timeout
    n.node_config = cfg
    return n


def test_get_run_pool_uses_vc_lib_overlay(tmp_path, monkeypatch):
    """The pool is created with ``pythonpath == VC_LIB_OVERLAY``."""
    monkeypatch.setenv("VC_LIB_OVERLAY", "/opt/lib-overlay")
    monkeypatch.delenv("VC_SANDBOX_PYTHON_PATHS", raising=False)
    extra: dict = {"run_dir": str(tmp_path)}
    pool = CodeNode._get_run_pool(extra)
    try:
        assert pool._pythonpath == "/opt/lib-overlay"
        assert pool._env["PYTHONPATH"] == ""
        assert pool._env["VC_CODE_PYTHONPATH"] == "/opt/lib-overlay"
    finally:
        pool.close()


def test_get_run_pool_empty_when_unset(tmp_path, monkeypatch):
    """No ``VC_LIB_OVERLAY`` → pool pythonpath is "" (stdlib-only worker)."""
    monkeypatch.delenv("VC_LIB_OVERLAY", raising=False)
    monkeypatch.delenv("VC_SANDBOX_PYTHON_PATHS", raising=False)
    extra: dict = {"run_dir": str(tmp_path)}
    pool = CodeNode._get_run_pool(extra)
    try:
        assert pool._pythonpath == ""
        assert pool._env["PYTHONPATH"] == ""
        assert pool._env["VC_CODE_PYTHONPATH"] == ""
    finally:
        pool.close()


def test_worker_stdlib_only_when_overlay_unset(tmp_path, monkeypatch):
    """With ``VC_LIB_OVERLAY`` UNSET the worker imports a STDLIB module (json) but
    NOT a host-installed 3rd-party (numpy) — proving the host site-packages is no
    longer on the worker's import path."""
    pytest.importorskip("numpy")  # numpy IS installed on the host (the test host)
    monkeypatch.delenv("VC_LIB_OVERLAY", raising=False)
    monkeypatch.delenv("VC_SANDBOX_PYTHON_PATHS", raising=False)

    # stdlib import works.
    ok_code = (
        "import json\n"
        "def process_fn(inputs):\n"
        "    return {'v': json.dumps({'a': 1})}"
    )
    res_ok = _node(ok_code)({}, {}, {"run_dir": str(tmp_path)})
    assert res_ok["status"] == "success", res_ok.get("error_message")
    assert res_ok["output"] == {"v": '{"a": 1}'}

    # host-only 3rd-party import FAILS (not on the empty overlay path) → node error.
    bad_code = (
        "import numpy\n"
        "def process_fn(inputs):\n"
        "    return {'v': 1}"
    )
    res_bad = _node(bad_code)({}, {}, {"run_dir": str(tmp_path)})
    assert res_bad["status"] == "error"
    assert "numpy" in (res_bad.get("error_message") or "").lower() or \
        "modulenotfound" in (res_bad.get("error_message") or "").lower()


def test_platform_base_packages_are_available(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    (base / "vc_base_probe.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    monkeypatch.delenv("VC_LIB_OVERLAY", raising=False)
    monkeypatch.setenv("VC_SANDBOX_PYTHON_PATHS", str(base))

    code = (
        "import vc_base_probe\n"
        "def process_fn(inputs):\n"
        "    return {'value': vc_base_probe.VALUE}\n"
    )
    result = _node(code)({}, {}, {"run_dir": str(tmp_path)})
    assert result["status"] == "success"
    assert result["output"] == {"value": "base"}


def test_workflow_overlay_precedes_platform_base(tmp_path, monkeypatch):
    custom = tmp_path / "custom"
    base = tmp_path / "base"
    custom.mkdir()
    base.mkdir()
    (custom / "vc_dep_probe.py").write_text("VALUE = 'custom'\n", encoding="utf-8")
    (base / "vc_dep_probe.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    monkeypatch.setenv("VC_LIB_OVERLAY", str(custom))
    monkeypatch.setenv("VC_SANDBOX_PYTHON_PATHS", str(base))

    code = (
        "import vc_dep_probe\n"
        "def process_fn(inputs):\n"
        "    return {'value': vc_dep_probe.VALUE}\n"
    )
    result = _node(code)({}, {}, {"run_dir": str(tmp_path)})
    assert result["status"] == "success"
    assert result["output"] == {"value": "custom"}


def test_overlay_cannot_shadow_standard_library(tmp_path):
    """A transitive package with a stdlib-colliding name stays subordinate."""
    overlay = tmp_path / "overlay"
    overlay.mkdir()
    (overlay / "statistics.py").write_text(
        "def mean(values):\n    return 'shadowed'\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()

    from vibecanvas_engine.code_runner import CodeWorkerPool

    direct_pool = CodeWorkerPool(str(overlay), str(run_dir), max_workers=1)
    try:
        result = direct_pool.run(
            "import statistics\n"
            "def process_fn(inputs):\n"
            "    return {'mean': statistics.mean([1, 2])}\n",
            {},
            timeout=10,
        )
        assert result == {"status": "success", "output": {"mean": 1.5}}
    finally:
        direct_pool.close()
