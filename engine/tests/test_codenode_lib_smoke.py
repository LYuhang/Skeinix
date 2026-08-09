import os

import pytest

np = pytest.importorskip("numpy")

from vibecanvas_engine.nodes.code import CodeNode


def _node(process_fn: str) -> CodeNode:
    n = CodeNode.__new__(CodeNode)
    n._default_timeout = 60.0
    n.node_config = {"programming_language": "python", "process_fn": process_fn}
    return n


def test_numpy_run_roundtrip(tmp_path, monkeypatch):
    # The worker pool imports libraries only from the overlay path in
    # ``VC_LIB_OVERLAY`` (stdlib-only when unset) — NOT the host site-packages. So
    # to keep this a MEANINGFUL "a CodeNode importing a declared lib works" smoke
    # test, point ``VC_LIB_OVERLAY`` at the dir that actually holds numpy on the
    # host (i.e. simulate the overlay containing numpy). The test then proves the
    # worker imports numpy from THAT path, exactly as it will from a real overlay.
    overlay_dir = os.path.dirname(os.path.dirname(os.path.abspath(np.__file__)))
    monkeypatch.setenv("VC_LIB_OVERLAY", overlay_dir)

    # No jail: a CodeNode can `import numpy` and use real file I/O under cwd.
    a_path = os.path.join(str(tmp_path), "a.txt")
    code = (
        "import numpy as np\n"
        "def process_fn(i):\n"
        f"    np.savetxt({a_path!r}, [[1, 2], [3, 4]])\n"
        "    np.savetxt('b.txt', [[5, 6]])\n"
        f"    return {{'a': np.loadtxt({a_path!r}).tolist(), 'b_exists': True}}"
    )
    res = _node(code)({}, {}, {"run_dir": str(tmp_path)})
    assert res["status"] == "success", res.get("error_message")
    assert res["output"]["a"] == [[1.0, 2.0], [3.0, 4.0]]
    # relative path lands under the pool's cwd (== run_dir)
    assert (tmp_path / "a.txt").exists() and (tmp_path / "b.txt").exists()
