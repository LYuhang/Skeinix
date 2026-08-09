"""The retired TaskManager and local workers are deleted."""
import importlib
from pathlib import Path

import pytest


API_SRC = Path(__file__).resolve().parent.parent / "src" / "vibecanvas_api"


def test_task_manager_file_deleted():
    p = API_SRC / "managers" / "task_manager.py"
    assert not p.exists(), f"{p} should be deleted"


def test_workers_files_deleted_or_dir_gone():
    p = API_SRC / "workers"
    if p.exists():
        # Acceptable only if it's an empty package shell.
        contents = [f.name for f in p.iterdir() if f.name != "__pycache__"]
        assert contents in ([], ["__init__.py"]), \
            f"workers/ should be deleted or empty; contains {contents}"


@pytest.mark.parametrize("name", [
    "vibecanvas_api.managers.task_manager",
    "vibecanvas_api.workers.workflow_exec",
    "vibecanvas_api.workers.batch_exec",
])
def test_module_no_longer_importable(name):
    """ImportError = deleted. (Not just `hasattr(False)` — gone from sys.modules state.)"""
    with pytest.raises((ImportError, ModuleNotFoundError)):
        importlib.import_module(name)


def test_no_remaining_taskmanager_refs():
    """Grep the source tree for any leftover symbol references."""
    import subprocess
    r = subprocess.run(
        ["grep", "-rln", "--include=*.py", "TaskManager",
         str(API_SRC), str(API_SRC.parent.parent / "tests")],
        capture_output=True, text=True,
    )
    # The only acceptable matches are in this test file itself (the string literal).
    leftovers = [
        line for line in r.stdout.strip().splitlines()
        if not line.endswith("test_phase6_taskmanager_deleted.py")
    ]
    assert leftovers == [], "Remaining TaskManager refs:\n  " + "\n  ".join(leftovers)
