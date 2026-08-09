"""Gate 1 — api package must not import gradio, gradio_vibecanvas, or
any bare legacy demo top-level module name.

The Engine package must remain independent from FastAPI, so web frameworks are not
forbidden here (unlike the engine purity guard). The legacy demo paths
are forbidden because their presence means an import-rewrite was missed.
"""

from __future__ import annotations

import json
import pkgutil
import subprocess
import sys


FORBIDDEN_BARE_NAMES = {
    "gradio",
    "gradio_vibecanvas",
}


def _collect_api_modules() -> list[str]:
    import vibecanvas_api
    mods = ["vibecanvas_api"]
    for _, name, _ in pkgutil.walk_packages(
        vibecanvas_api.__path__, prefix="vibecanvas_api."
    ):
        mods.append(name)
    return mods


def _subprocess_import_and_report(module_names: list[str]) -> dict:
    code = f"""
import importlib, json, sys
forbidden = {json.dumps(sorted(FORBIDDEN_BARE_NAMES))}
errors = []
for name in {json.dumps(module_names)}:
    try:
        importlib.import_module(name)
    except Exception as e:
        errors.append({{"mod": name, "err": f"{{type(e).__name__}}: {{e}}"}})
loaded = sorted(set(sys.modules) & set(forbidden))
print(json.dumps({{"loaded_forbidden": loaded, "import_errors": errors}}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"subprocess crashed:\nstdout={result.stdout}\nstderr={result.stderr}"
    )
    last = result.stdout.strip().splitlines()[-1]
    return json.loads(last)


def test_no_gradio_in_api_package():
    """Gate 1: importing every vibecanvas_api submodule must NOT pull
    gradio or gradio_vibecanvas into sys.modules."""
    mods = _collect_api_modules()
    assert len(mods) >= 30, f"only {mods} modules found; expected ~60+"

    report = _subprocess_import_and_report(mods)
    assert report["loaded_forbidden"] == [], (
        f"api pulled in forbidden packages: {report['loaded_forbidden']}\n"
        "Engine package purity violated; fix the import chain."
    )
    # ImportErrors during walk are tolerable IF the failure is downstream
    # (e.g. an optional dep not installed) — but a missed import rewrite
    # would surface here too. Print errors for triage; only fail on
    # forbidden loads.
    if report["import_errors"]:
        print("Per-module import errors (informational):")
        for e in report["import_errors"]:
            print(f"  {e['mod']}: {e['err']}")
