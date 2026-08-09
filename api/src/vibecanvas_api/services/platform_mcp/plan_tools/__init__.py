"""LangChain-only Platform MCP surface for Dynamic Execution Plans."""

from .create_execution_plan import create_execution_plan

PLAN_TOOLS = [create_execution_plan]

__all__ = ["PLAN_TOOLS", "create_execution_plan"]
