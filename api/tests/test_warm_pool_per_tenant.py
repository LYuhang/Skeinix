"""RE-6 Warm-prod T2 — per-tenant ``WarmGvisorPool`` (HARD cross-tenant isolation).

The minimal ``WarmGvisorPool`` binds the WHOLE run-root
(``{store_root}/run`` → ``/runs``) → SOFT cross-tenant isolation only (the gofer
serves every tenant's subtree). A PER-TENANT pool binds ONLY
``{store_root}/run/{tenant}`` → ``/runs``, so another tenant's dir is NOT in the
sandbox's filesystem — cross-tenant access is PHYSICALLY impossible (ENOENT),
not policy-enforced.

These tests REQUIRE real runsc (the boundary IS the mount — a fake provider
cannot prove the gofer never served tenant B's tree). They mirror
``test_warm_pool.py`` helpers but construct ``WarmGvisorPool(tenant="A", ...)``.
"""

from __future__ import annotations

import os

import pytest

from vibecanvas_api.services.object_store import FilesystemObjectStore
from vibecanvas_api.services.sandbox import _gvisor_runnable
from vibecanvas_api.services.sandbox.gvisor import RootlessGvisorProvider
from vibecanvas_api.services.sandbox.warm import WarmGvisorPool

pytestmark = pytest.mark.skipif(
    not _gvisor_runnable(), reason="rootless gVisor not runnable here"
)


# ---------------------------------------------------------------------------
# workflows
# ---------------------------------------------------------------------------
def _start_end_wf() -> dict:
    """The trivial pure Start→End wf (passthrough an integer)."""
    return {
        "__meta__": {
            "workflow_id": "wf_pt",
            "workflow_name": "per_tenant_smoke",
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
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "x": {"type": "integer", "value": 0, "reference": "__start__.x"}
            },
            "output_fields": {"x": {"type": "integer", "description": "passthrough"}},
            "node_config": {},
            "children": [],
        },
    }


def _probe_wf() -> dict:
    """A Start→Code→End wf whose CodeNode probes the sandbox filesystem:
    - ``os.listdir('/runs')`` — what tenant subtrees are mounted? (the headline:
      a tenant-A pool must show ONLY A's runs, never tenant B).
    - tries to READ ``/runs/../B/secret/data.txt`` (a path escaping /runs toward
      the host-seeded tenant-B secret) — must FAIL (FileNotFoundError, or the
      jailed-open PermissionError) → it CANNOT read "B-SECRET".
    """
    process_fn = (
        "def process_fn(inputs):\n"
        "    import os\n"
        "    ls = os.listdir('/runs')\n"
        "    read_ok = False\n"
        "    read_val = ''\n"
        "    try:\n"
        "        f = open('/runs/../B/secret/data.txt')\n"
        "        read_val = f.read()\n"
        "        f.close()\n"
        "        read_ok = True\n"
        "    except Exception as e:\n"
        "        read_val = str(e)\n"
        "    return {'ls': ls, 'read_ok': read_ok, 'read_val': read_val}\n"
    )
    return {
        "__meta__": {
            "workflow_id": "wf_probe",
            "workflow_name": "probe",
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
            "node_name": "prober",
            "node_type": "CodeNode",
            "node_description": "probe the mounted /runs tree",
            "input_fields": {
                "x": {"type": "integer", "value": 0, "reference": "__start__.x"},
            },
            "output_fields": {
                "ls": {"type": "array", "description": "listdir /runs"},
                "read_ok": {"type": "boolean", "description": "could read B's secret"},
                "read_val": {"type": "string", "description": "content or exc name"},
            },
            "node_config": {
                "programming_language": "python",
                "process_fn": process_fn,
            },
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "ls": {"type": "array", "value": [], "reference": "prober.ls"},
                "read_ok": {"type": "boolean", "value": False, "reference": "prober.read_ok"},
                "read_val": {"type": "string", "value": "", "reference": "prober.read_val"},
            },
            "output_fields": {
                "ls": {"type": "array", "description": "listdir /runs"},
                "read_ok": {"type": "boolean", "description": "could read B's secret"},
                "read_val": {"type": "string", "description": "content or exc name"},
            },
            "node_config": {},
            "children": [],
        },
    }


# ---------------------------------------------------------------------------
# helpers (mirror test_warm_pool.py, but per-tenant)
# ---------------------------------------------------------------------------
def _resolve() -> str:
    from vibecanvas_api.services.sandbox import _resolve_runsc

    return _resolve_runsc()


def _fs_store_pool(tmp_path, monkeypatch, tenant: str) -> WarmGvisorPool:
    """A per-tenant ``WarmGvisorPool`` wired to a real provider + a filesystem
    object store rooted at ``store_root`` (shared host root; each pool mounts
    only its own ``{store_root}/run/{tenant}`` subtree)."""
    store_root = str(tmp_path / "store")
    work_root = str(tmp_path / "work" / tenant)
    monkeypatch.setattr(
        "vibecanvas_api.services.sandbox.warm.get_object_store",
        lambda: FilesystemObjectStore(root=store_root),
    )
    return WarmGvisorPool(
        provider=RootlessGvisorProvider(_resolve()),
        store_root=store_root,
        work_root=work_root,
        tenant=tenant,
    )


# ===========================================================================
# tests
# ===========================================================================
def test_per_tenant_pool_runs_and_lands_under_tenant_subtree(tmp_path, monkeypatch):
    """A tenant-A pool runs a pure Start→End wf → correct result + the run lands
    at ``{store_root}/run/A/{run_id}/__exec__/result.json`` (NO tenant prefix
    under it — the mount IS the tenant dir)."""
    pool = _fs_store_pool(tmp_path, monkeypatch, "A")
    # _runs_root is the tenant subtree, NOT the shared root.
    assert pool._runs_root == os.path.join(str(tmp_path / "store"), "run", "A")
    pool.start()
    try:
        res = pool.submit(
            workflow=_start_end_wf(),
            inputs={"x": 5},
            run_id="r_a",
            tenant="A",
            timeout=60.0,
        )
        assert res.error_dict == {}, f"engine errors: {res.error_dict}"
        assert res.final_outputs["__end__"]["x"] == 5
        result = os.path.join(
            str(tmp_path / "store"), "run", "A", "r_a", "__exec__", "result.json"
        )
        assert os.path.exists(result), f"result.json not at the per-tenant path: {result}"
    finally:
        pool.stop()


def test_hard_cross_tenant_isolation(tmp_path, monkeypatch):
    """THE HEADLINE (N4): a tenant-A worker genuinely CANNOT see tenant B's tree.

    Seed a host secret for tenant B at ``{store_root}/run/B/secret/data.txt``.
    Boot a tenant-A pool (mounts ONLY ``{store_root}/run/A`` → /runs). Run a
    CodeNode that lists ``/runs`` and tries to read ``/runs/../B/secret/data.txt``:
    - the listing of /runs contains ONLY A's run(s), NEVER "B" (B's tree was
      never bind-mounted → it does not exist in the sandbox's FS),
    - the read attempt FAILS (cannot retrieve "B-SECRET").
    """
    store_root = str(tmp_path / "store")
    # Seed tenant B's secret ON THE HOST (a sibling subtree of A's mount).
    b_secret = os.path.join(store_root, "run", "B", "secret")
    os.makedirs(b_secret, exist_ok=True)
    with open(os.path.join(b_secret, "data.txt"), "w", encoding="utf-8") as f:
        f.write("B-SECRET")

    pool = _fs_store_pool(tmp_path, monkeypatch, "A")
    pool.start()
    try:
        res = pool.submit(
            workflow=_probe_wf(),
            inputs={"x": 1},
            run_id="probe_a",
            tenant="A",
            timeout=60.0,
        )
        assert res.error_dict == {}, f"probe engine errors: {res.error_dict}"
        out = res.final_outputs["__end__"]
        ls = out["ls"]
        print(f"\n[RE-6 Warm-prod T2] tenant-A worker /runs listing = {ls}")
        print(f"[RE-6 Warm-prod T2] B-secret read_ok={out['read_ok']} read_val={out['read_val']!r}")

        # The mount boundary: B's tree is NOT in tenant-A's sandbox.
        assert "B" not in ls, f"LEAK: tenant-A sandbox sees tenant B in /runs: {ls}"
        # A's own run IS present (sanity — the mount is the tenant subtree).
        assert "probe_a" in ls, f"tenant-A's own run missing from /runs: {ls}"
        # The read attempt could NOT retrieve B's secret.
        assert out["read_ok"] is False, "LEAK: tenant-A read tenant B's secret file"
        assert out["read_val"] != "B-SECRET", f"LEAK: secret contents exposed: {out['read_val']!r}"
    finally:
        pool.stop()


def test_two_pools_independent_runs_roots(tmp_path, monkeypatch):
    """Two per-tenant pools (A, B) have disjoint ``_runs_root`` mounts; A's pool
    runs land under A's subtree, B's under B's — neither sees the other."""
    pool_a = _fs_store_pool(tmp_path, monkeypatch, "A")
    pool_b = _fs_store_pool(tmp_path, monkeypatch, "B")
    store_root = str(tmp_path / "store")
    assert pool_a._runs_root == os.path.join(store_root, "run", "A")
    assert pool_b._runs_root == os.path.join(store_root, "run", "B")
    assert pool_a._runs_root != pool_b._runs_root

    pool_a.start()
    pool_b.start()
    try:
        ra = pool_a.submit(
            workflow=_start_end_wf(), inputs={"x": 1}, run_id="ind_a", tenant="A", timeout=60.0
        )
        rb = pool_b.submit(
            workflow=_start_end_wf(), inputs={"x": 2}, run_id="ind_b", tenant="B", timeout=60.0
        )
        assert ra.error_dict == {} and rb.error_dict == {}
        assert ra.final_outputs["__end__"]["x"] == 1
        assert rb.final_outputs["__end__"]["x"] == 2
        pa = os.path.join(store_root, "run", "A", "ind_a", "__exec__", "result.json")
        pb = os.path.join(store_root, "run", "B", "ind_b", "__exec__", "result.json")
        assert os.path.exists(pa) and os.path.exists(pb)
        # A's run must NOT land under B's subtree and vice-versa.
        assert not os.path.exists(os.path.join(store_root, "run", "B", "ind_a"))
        assert not os.path.exists(os.path.join(store_root, "run", "A", "ind_b"))
    finally:
        pool_a.stop()
        pool_b.stop()
