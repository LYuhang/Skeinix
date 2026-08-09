"""Installed Agent Runtime adapters.

Persistence schemas may know future runtime types, but only adapters listed here
may be selected or launched.  This prevents a UI flag from silently routing a
Codex chat through LangChain.
"""

AVAILABLE_RUNTIME_TYPES = frozenset({"langchain", "codex"})
