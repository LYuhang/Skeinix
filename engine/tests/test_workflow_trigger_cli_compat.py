"""Workflow.trigger preserves tuple compatibility and rejects a running loop."""
import asyncio

import pytest

from vibecanvas_engine.workflow import Workflow


def test_trigger_returns_legacy_tuple(simple_workflow_dict):
    """Sync entry — returns (previous_outputs, error_dict, exec_time)."""
    wf = Workflow(simple_workflow_dict)
    out, errors, exec_time = wf.trigger({})
    assert isinstance(out, dict)
    assert isinstance(errors, dict)
    assert isinstance(exec_time, (int, float))


def test_trigger_under_running_loop_raises_cleanly(simple_workflow_dict):
    """Calling trigger() inside a running event loop must raise RuntimeError
    with a helpful message — never silently corrupt."""
    wf = Workflow(simple_workflow_dict)

    async def run():
        with pytest.raises(RuntimeError, match=r"running event loop|astream"):
            wf.trigger({})

    asyncio.run(run())


def test_multiprocessing_imports_gone():
    """workflow.py must not import multiprocessing nor reference _run_in_subprocess."""
    import vibecanvas_engine.workflow as wf_mod
    src = open(wf_mod.__file__).read()
    assert "import multiprocessing" not in src, "multiprocessing import lingers (T8)"
    assert "from multiprocessing" not in src, "multiprocessing import lingers (T8)"
    assert "_run_in_subprocess" not in src, "_run_in_subprocess symbol lingers (T8)"
