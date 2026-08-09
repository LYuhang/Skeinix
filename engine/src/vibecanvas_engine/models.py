"""Built-in LLM models.

Import this module to register the default models into ``llm_registry``.
These are echo/mock implementations — they return a JSON string echoing
the last user turn without making any network call. Replace or extend
them with real LLM implementations for production use.

Usage:
    import core.models  # registers all built-in models
    # or
    from core.models import register_builtin_models
    register_builtin_models()
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .register import BaseLLM, llm_registry


class EchoLLM(BaseLLM):
    """Echo LLM that returns a JSON string mirroring the last user message.

    Subclass and override ``model_label`` to create named variants,
    or use ``register_builtin_models()`` to register the defaults.
    """

    model_label: str = "echo"

    def __call__(
        self,
        conversation_dict: Dict[str, Any],
        inference_config: Optional[Dict[str, Any]] = None,
        stop_event: Optional[Any] = None,
    ) -> str:
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("LLM call cancelled")
        turns = (conversation_dict or {}).get("conversations") or []
        last_user = ""
        for t in reversed(turns):
            if (t.get("from") or "").lower() in ("human", "user"):
                last_user = t.get("value") or ""
                break
        return json.dumps(
            {
                "model": self.model_label,
                "echo": last_user,
                "note": "mock response — no network call was made",
            },
            ensure_ascii=False,
        )


# ---------------------------------------------------------------------------
# Built-in model definitions
# ---------------------------------------------------------------------------

_BUILTIN_MODELS = {
    # "gpt-5.4": "gpt-5.4",
    # "gpt-4o": "gpt-4o",
    # "claude-opus-4.6": "claude-opus-4.6",
}


def register_builtin_models():
    """Register all built-in echo models into llm_registry."""
    for name, label in _BUILTIN_MODELS.items():
        if name not in llm_registry._module_dict:
            cls = type(f"_Echo_{name}", (EchoLLM,), {"model_label": label})
            llm_registry._module_dict[name] = cls


# Auto-register on import
register_builtin_models()
