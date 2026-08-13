"""User-facing metadata for the built-in Platform MCP services.

The transport registry and the management UI both consume this table.  Keep
tool membership out of this module: ``server.py`` joins this metadata with the
actual registered tool objects so the catalog can never report a stale count.
"""

from __future__ import annotations

from typing import Final


PLATFORM_MCP_METADATA: Final[dict[str, dict[str, object]]] = {
    "config": {
        "name": "Configuration",
        "description": "Read safe platform, model, current Chat, and workflow configuration.",
        "activation": "Always available",
        "activation_mode": "base",
        "runtime_types": ["langchain", "codex"],
    },
    "interactive": {
        "name": "Interactive",
        "description": "Render durable rich content and optionally pause for an explicit Continue action.",
        "activation": "Always available",
        "activation_mode": "base",
        "runtime_types": ["langchain", "codex"],
    },
    "workflow": {
        "name": "Workflow",
        "description": "List and inspect workflows that the current user is authorized to access.",
        "activation": "Always available",
        "activation_mode": "base",
        "runtime_types": ["langchain", "codex"],
    },
    "task": {
        "name": "Task",
        "description": "Inspect and manage Task Center work, scheduled runs, cancellation, and resume.",
        "activation": "/task",
        "activation_mode": "command",
        "runtime_types": ["langchain", "codex"],
    },
    "deployment": {
        "name": "Deployment",
        "description": "List, inspect, create, update, and remove authorized workflow deployments.",
        "activation": "/deployment",
        "activation_mode": "command",
        "runtime_types": ["langchain", "codex"],
    },
    "knowledge": {
        "name": "Knowledge",
        "description": "Discover and semantically search knowledge bases available to the current user.",
        "activation": "/knowledge",
        "activation_mode": "command",
        "runtime_types": ["langchain", "codex"],
    },
    "build": {
        "name": "Build & Run",
        "description": "Create, validate, version, update, and execute workflows in the current Chat.",
        "activation": "/build",
        "activation_mode": "command",
        "runtime_types": ["langchain", "codex"],
    },
    "plan": {
        "name": "Execution Plan",
        "description": "Validate and submit a durable dynamic execution plan for LangChain subagents.",
        "activation": "/plan",
        "activation_mode": "command",
        "runtime_types": ["langchain"],
    },
    "diagram": {
        "name": "Diagram",
        "description": "Author, validate, present, visually review, and export semantic diagrams on an infinite canvas.",
        "activation": "/diagram",
        "activation_mode": "command",
        "runtime_types": ["langchain", "codex"],
    },
}


def platform_mcp_description(server: str) -> str:
    """Return the canonical Runtime-facing description for ``server``."""
    metadata = PLATFORM_MCP_METADATA.get(server)
    if metadata is None:
        raise ValueError(f"unknown platform MCP capability: {server}")
    return str(metadata["description"])
