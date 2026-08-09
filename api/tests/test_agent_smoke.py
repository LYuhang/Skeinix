"""Gate 3 — agent module imports and constructs without a live LLM."""

from __future__ import annotations

import inspect


def test_agent_module_imports():
    """agent.py + its dependencies (prompts, tools, middleware) load."""
    from vibecanvas_api.agent import run_agent_turn, _get_or_create_agent
    assert callable(run_agent_turn)
    assert callable(_get_or_create_agent)
    # MCP T4: _get_or_create_agent went async (per-tenant MCP load).
    # ``callable()`` alone passes for any function — guard the async
    # signature explicitly so a future sync-revert is caught here.
    assert inspect.iscoroutinefunction(_get_or_create_agent)


def test_middleware_classes_importable():
    """LangGraph middleware classes load (verbatim from legacy)."""
    from vibecanvas_api.agents.middleware.context_prefix_strip import (
        ContextPrefixStripEdit,
    )
    from vibecanvas_api.agents.middleware.lifecycle_policy import (
        LifecyclePolicyEdit,
    )
    assert ContextPrefixStripEdit is not None
    assert LifecyclePolicyEdit is not None
