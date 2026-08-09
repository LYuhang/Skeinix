"""Subagent output helpers.

``run_bounded_agent`` defines the terminal ``set_output`` tool at execution time
so it can close over that node's ``output_fields``. This module only keeps the
pure projection helper shared by normal/error/incomplete paths.
"""
from __future__ import annotations

import json


def coerce_to_fields(payload: dict, output_fields: dict) -> dict:
    """Project a payload dict onto the declared output_fields.

    * Present key → keep value; JSON-encode dict/list to string.
    * Missing key  → empty string default.
    * Extra keys   → dropped.

    Used by ``run_bounded_agent`` for error/incomplete paths where the
    tool was never called and we need a consistent empty result shape.
    """
    coerced: dict = {}
    for name in output_fields:
        v = payload.get(name)
        if isinstance(v, (dict, list)):
            coerced[name] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            coerced[name] = ""
        else:
            coerced[name] = v
    return coerced
