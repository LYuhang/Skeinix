"""Spot-check the lean PythonSandbox.evaluate used by Condition/Transform nodes.

The old CodeNode jail (``execute_sync`` plus ProcessPool and ``open``
jail) is gone. What remains is the small restricted-expression evaluator used
for flow-control template expressions. The live surface is:

    sb = PythonSandbox(libraries_config: dict | None = None)
    sb.evaluate(expression: str) -> Any
"""

from __future__ import annotations

import pytest

from vibecanvas_engine import PythonSandbox, SecurityError


def test_sandbox_evaluate_simple_expression():
    sb = PythonSandbox()
    assert sb.evaluate("2 + 2") == 4


def test_sandbox_evaluate_uses_whitelisted_library():
    """Libraries declared in the config are pre-loaded and used by name."""
    sb = PythonSandbox({"math": "math"})
    assert sb.evaluate("math.sqrt(16.0)") == 4.0


def test_sandbox_evaluate_blocks_import_statement():
    """An `import` in a template expression is forbidden."""
    sb = PythonSandbox({"os": "os"})
    with pytest.raises((SecurityError, ValueError, RuntimeError)):
        sb.evaluate("__import__('os').getcwd()")


def test_sandbox_evaluate_blocks_dunder_access():
    sb = PythonSandbox()
    with pytest.raises((SecurityError, RuntimeError)):
        sb.evaluate("(1).__class__")
