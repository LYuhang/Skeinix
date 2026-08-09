"""Gate 1 — Invariant 2.1: engine MUST NOT pull in any web framework.

This is the load-bearing purity guard. Spawns a fresh Python
subprocess (clean sys.modules), imports every module under
vibecanvas_engine, and asserts that none of the forbidden web
packages got loaded as a transitive consequence.

A failure means someone (intentionally or not) added a web-stack
dependency to the engine. Don't relax this test — investigate the
import chain and remove the offending dep.
"""

from __future__ import annotations

import json
import pkgutil
import subprocess
import sys


FORBIDDEN = {
    "gradio",
    "fastapi",
    "flask",
    "django",
    "starlette",
    "gradio_vibecanvas",
    "uvicorn",
    "tornado",
    "bottle",
    "sanic",
}


def _collect_engine_modules() -> list[str]:
    """Return every importable module path under vibecanvas_engine."""
    import vibecanvas_engine
    mods = ["vibecanvas_engine"]
    for _, name, _ in pkgutil.walk_packages(
        vibecanvas_engine.__path__, prefix="vibecanvas_engine."
    ):
        mods.append(name)
    return mods


def _subprocess_import_and_check(module_names: list[str]) -> dict:
    """Run a clean python process that imports the named modules
    and reports which sys.modules entries match FORBIDDEN."""
    code = f"""
import importlib, json, sys
forbidden = {json.dumps(sorted(FORBIDDEN))}
for name in {json.dumps(module_names)}:
    importlib.import_module(name)
loaded = sorted(set(sys.modules) & set(forbidden))
print(json.dumps({{"loaded_forbidden": loaded}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        f"subprocess import crashed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    last_line = result.stdout.strip().splitlines()[-1]
    return json.loads(last_line)


def test_no_web_framework_in_engine():
    """Gate 1: importing vibecanvas_engine + every submodule must NOT
    pull in any web framework or the legacy gradio_vibecanvas package."""
    mods = _collect_engine_modules()
    assert len(mods) >= 20, (
        f"only found {len(mods)} engine modules; expected ~22"
    )
    report = _subprocess_import_and_check(mods)
    assert report["loaded_forbidden"] == [], (
        f"engine pulled in forbidden packages: {report['loaded_forbidden']}\n"
        "Invariant 2.1 violated — the engine must be web-framework-free.\n"
        "Investigate the import chain: which module pulled this in?"
    )
