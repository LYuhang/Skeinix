"""JSON parser with stable JSON-path citation metadata."""
from __future__ import annotations

import json
from typing import Any

from .base import EmptyDocumentError, ParsedSegment, ParseError, Parser
from .txt import TxtParser


MAX_JSON_DEPTH = 32


def _flatten(value: Any, path: str, depth: int, out: list[str]) -> None:
    if depth > MAX_JSON_DEPTH:
        raise ParseError("JSON nesting exceeds the safety limit")
    if isinstance(value, dict):
        for key, child in value.items():
            _flatten(child, f"{path}.{key}" if path else str(key), depth + 1, out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _flatten(child, f"{path}[{index}]", depth + 1, out)
    elif value is not None:
        out.append(f"{path or '$'}: {value}")


class JsonParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        try:
            value = json.loads(TxtParser._decode(blob))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise ParseError(f"Cannot parse JSON: {exc}") from exc
        roots = value if isinstance(value, list) else [value]
        segments: list[ParsedSegment] = []
        for index, item in enumerate(roots):
            lines: list[str] = []
            root_path = f"$[{index}]" if isinstance(value, list) else "$"
            _flatten(item, root_path, 0, lines)
            if lines:
                segments.append(
                    ParsedSegment(text="\n".join(lines), metadata={"json_path": root_path})
                )
        if not segments:
            raise EmptyDocumentError("JSON has no searchable scalar values")
        return segments
