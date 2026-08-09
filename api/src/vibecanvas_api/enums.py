"""Central source of truth for every static frontend dropdown.

Dropdowns whose options depend on the live workflow graph (reference
pickers, paired-node selectors, branch target selectors) stay on the
frontend — they can only be computed against the current graph. The
ones that are static or that come from registries live here so the
backend and the canvas always agree.

Call ``load_config_and_sync`` once at app startup: it reads
``demo/config.yaml``, applies the ``programming_languages`` entry to
``core.node._CODE_LANGUAGES`` (which is referenced by
``CodeNode.CONFIG_SCHEMA`` by identity), and snapshots the current
``llm_registry`` into ``core.node._PROMPT_MODELS``. After that, both
``get_frontend_enums`` and ``CodeNode/PromptNode.CONFIG_SCHEMA`` return
the same data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import yaml

from vibecanvas_engine.register import llm_registry
from vibecanvas_engine.node import (
    set_programming_languages,
    set_prompt_models,
    set_code_timeout,
    get_programming_languages,
    get_prompt_models,
    get_code_timeout,
)


CONFIG_PATH = Path(__file__).parent / "config.yaml"


# -- hardcoded / preset enums -------------------------------------------------
# This list is constitutional: it's baked into jsonschema everywhere
# (GENERAL_NODE_SCHEMA for the field type enum) and changing it is a breaking
# change to the workflow engine, not a config flip.

FIELD_TYPES: List[str] = [
    "string",
    "number",
    "integer",
    "boolean",
    "array",
    "object",
]


# -- config-driven state ------------------------------------------------------
# These two are cached module-level so ``get_frontend_enums`` is cheap to
# call from every request. ``load_config_and_sync`` is the only thing that
# writes them, and it runs once during app bootstrap.

_WORKFLOW_DOMAINS: List[str] = ["public", "indomain"]


def load_config_and_sync(config_path: Path | str | None = None) -> Dict[str, Any]:
    """Read ``config.yaml`` and push its contents into the core schemas.

    Returns the parsed config dict so callers can log what was loaded.
    Missing or unreadable config falls back to the existing defaults.
    """
    global _WORKFLOW_DOMAINS
    path = Path(config_path or CONFIG_PATH)
    cfg: Dict[str, Any] = {}
    if path.is_file():
        try:
            cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            print(f"[enums] failed to parse {path}: {e}")
            cfg = {}

    langs = cfg.get("programming_languages") or []
    if isinstance(langs, list) and langs:
        set_programming_languages([str(x) for x in langs])

    # CodeNode third-party libraries are no longer a config-driven enum: they
    # come from the per-workflow dependency overlay. Any leftover
    # ``code_libraries`` key in a config file is silently ignored.

    code_timeout = cfg.get("code_timeout")
    if code_timeout is not None:
        set_code_timeout(float(code_timeout))

    # ``code_inline_mode`` was a stop-gap for the fork-and-spawn
    # deadlock when a CodeNode pool was nested inside a multiprocessing
    # workflow subprocess. With the new async engine (T6/T7) CodeNode runs
    # via ``loop.run_in_executor(ProcessPool, ...)`` directly in the
    # workflow process, so the toggle is gone from sandbox.py — silently
    # ignore any leftover ``code_inline_mode`` key in user config files.

    domains = cfg.get("workflow_domains") or []
    if isinstance(domains, list) and domains:
        _WORKFLOW_DOMAINS = [str(x) for x in domains]

    models = list(llm_registry._module_dict.keys())
    if models:
        set_prompt_models(models)

    global _AGENT_CONFIG
    _AGENT_CONFIG = cfg.get("agent") or {}

    return cfg


# -- agent config cache --------------------------------------------------------

_AGENT_CONFIG: Dict[str, Any] = {}


def get_agent_config() -> Dict[str, Any]:
    return dict(_AGENT_CONFIG)


def get_frontend_enums() -> Dict[str, List[str]]:
    """Return the full dropdown payload for the frontend.

    The shape is deliberately flat — one key per dropdown — so the Svelte
    side can consume it as ``enums.field_types``, ``enums.model_names``
    etc. without walking a nested structure.
    """
    model_names = get_prompt_models()
    try:
        from vibecanvas_api.services.llm_credentials_inject import (
            PLATFORM_DEFAULT_MODEL_NAME,
            platform_default_credential_entry,
        )
        if platform_default_credential_entry() and PLATFORM_DEFAULT_MODEL_NAME not in model_names:
            model_names = [PLATFORM_DEFAULT_MODEL_NAME, *model_names]
    except Exception:
        pass
    return {
        "field_types": list(FIELD_TYPES),
        "programming_languages": get_programming_languages(),
        "model_names": model_names,
        "workflow_domains": list(_WORKFLOW_DOMAINS),
    }


def build_runtime_vars() -> Dict[str, str]:
    """Build placeholder substitution dict for AGENT_SPEC / SYSTEM PROMPT generation.

    Values come from config.yaml via the core schema setters.
    Keys match {placeholder} patterns used in AGENT_SPEC fields.
    """
    return {
        "code_timeout": str(int(get_code_timeout())),
        "prompt_models": ", ".join(f"`{m}`" for m in get_prompt_models()),
        "programming_languages": ", ".join(get_programming_languages()),
    }
