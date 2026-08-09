"""Snapshot test: pin the public surface of vibecanvas_engine.

A regression here (export removed, renamed, or replaced) breaks
consumers. Bump the protocol or coordinate with downstream rather
than editing this test.
"""

from __future__ import annotations

import vibecanvas_engine


def test_version_is_pep440_ish():
    """__version__ is a non-empty string."""
    v = vibecanvas_engine.__version__
    assert isinstance(v, str) and v


def test_workflow_is_exported():
    from vibecanvas_engine import Workflow
    assert Workflow.__module__ == "vibecanvas_engine.workflow"


def test_all_14_node_classes_exported():
    from vibecanvas_engine import (
        BaseNode, StartNode, EndNode, CodeNode,
        PromptNode, ConditionNode, ParallelStartNode, ParallelEndNode,
        LoopBeginNode, LoopEndNode, HTTPRequestNode, TransformNode,
        TemplateNode, TableReadNode, TableWriteNode,
    )
    for cls in [StartNode, EndNode, CodeNode, PromptNode,
                ConditionNode, ParallelStartNode, ParallelEndNode,
                LoopBeginNode, LoopEndNode, HTTPRequestNode, TransformNode,
                TemplateNode, TableReadNode, TableWriteNode]:
        assert issubclass(cls, BaseNode), f"{cls.__name__} is not a BaseNode subclass"


def test_registries_exported_and_populated():
    from vibecanvas_engine import llm_registry, node_registry, Registry
    assert isinstance(llm_registry, Registry)
    assert isinstance(node_registry, Registry)
    # node_registry should be populated by side-effect of __init__ import
    registered = node_registry.list_all()
    assert len(registered) >= 14, f"only {len(registered)} classes registered: {registered}"


def test_sandbox_symbols_exported():
    # ``run_in_sandbox_pool`` and the old CodeNode jail are gone; only the
    # lean template-expression evaluator remains on the public surface.
    from vibecanvas_engine import PythonSandbox, SecurityError
    assert callable(PythonSandbox)
    assert issubclass(SecurityError, Exception)


def test_models_exported():
    from vibecanvas_engine import EchoLLM, register_builtin_models, BaseLLM
    assert issubclass(EchoLLM, BaseLLM)
    assert callable(register_builtin_models)


def test_scope_utilities_exported():
    from vibecanvas_engine import (
        safe_call_with_args, recursive_get, scoped_recursive_get,
        walk_to_scope, build_scope_chain,
    )
    assert all(callable(f) for f in (
        safe_call_with_args, recursive_get, scoped_recursive_get,
        walk_to_scope, build_scope_chain,
    ))
