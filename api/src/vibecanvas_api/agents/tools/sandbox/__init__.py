"""agents/tools/sandbox — the agent's sandbox execution tools.

A clean per-(tenant, wf) Linux sandbox: ``bash`` runs shell commands (network on
per the configured posture, so ``pip install`` etc. work and persist into the
per-wf overlay). It is a standard ``@tool`` that resolves its sandbox session from
``runtime.context`` (the same accessor the fs tools use).
"""
from vibecanvas_api.agents.tools.sandbox.bash import bash

SANDBOX_TOOLS = [bash]

__all__ = ["bash", "SANDBOX_TOOLS"]
