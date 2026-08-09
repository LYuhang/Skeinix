"""The sole runtime truth for enabled VibeDiagram types."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from .models import DiagramDocument

REGISTRY_VERSION = "2026.08.1"
PROTOCOL_VERSION = 1
COMPILER_VERSION = "1.2.0"
THEME_VERSION = "1.0.0"

# These are protocol tokens, not renderer colors.  Every authoring surface,
# validator and renderer resolves the same finite catalog.
STYLE_ROLES = (
    "neutral",
    "primary",
    "secondary",
    "success",
    "warning",
    "danger",
    "actor",
    "service",
    "storage",
    "event",
    "external",
    "note",
)

ASSET_CATALOG = {
    "platform.database": {
        "title": "Database",
        "asset_kind": "shape",
        "compatible_node_kinds": ("database", "storage"),
        "style_role": "storage",
    },
    "platform.queue": {
        "title": "Message queue",
        "asset_kind": "shape",
        "compatible_node_kinds": ("queue",),
        "style_role": "event",
    },
    "platform.cloud": {
        "title": "Cloud service",
        "asset_kind": "icon",
        "compatible_node_kinds": ("cloud", "service"),
        "style_role": "service",
    },
}

BASE_AUTHORING_INSTRUCTIONS = (
    "Create stable semantic IDs before relations and never derive identity from labels.",
    "Use explicit source and target node IDs for every edge.",
    "Keep ordinary node geometry out of the semantic model; express direction and relative intent instead.",
    "Use registered styleRole tokens and do not encode required meaning only with color.",
    "Use only registered relative constraints; never replace ordinary layout intent with guessed pixel coordinates.",
    "Write the complete auto-saved source under /data/diagrams and retain the filesystem content_hash.",
)

FORBIDDEN_PATTERNS = (
    "external URL or base64 data",
    "absolute geometry for ordinary initial nodes",
    "unregistered node kind, edge kind, styleRole, or asset key",
    "creating a separate publish copy instead of editing the canonical file",
)

ALLOWED_CONSTRAINTS = (
    "same-rank",
    "left-of",
    "right-of",
    "above",
    "below",
    "inside",
    "prefer-near",
    "prefer-apart",
    "order",
    "primary-path",
    "increase-gap",
    "route-above",
    "route-below",
)


@dataclass(frozen=True, slots=True)
class DiagramType:
    family: str
    type: str
    maturity: str
    use_when: str
    do_not_use_when: tuple[str, ...]
    semantic_focus: tuple[str, ...]
    allowed_node_kinds: tuple[str, ...]
    allowed_edge_kinds: tuple[str, ...]
    required_semantics: tuple[str, ...]
    quality_policy: dict[str, str]

    @property
    def key(self) -> str:
        return f"{self.family}/{self.type}"

    @property
    def spec_hash(self) -> str:
        payload = json.dumps({
            "registry": REGISTRY_VERSION,
            "base": DiagramDocument.model_json_schema(by_alias=True),
            "type": asdict(self),
            "authoring_instructions": BASE_AUTHORING_INSTRUCTIONS,
            "forbidden_patterns": FORBIDDEN_PATTERNS,
        }, sort_keys=True, separators=(",", ":"), default=list)
        return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"


_COMMON_NODE_KINDS = (
    "start", "end", "process", "decision", "actor", "person", "system",
    "application", "service", "component", "interface", "database", "storage",
    "queue", "event", "cloud", "device", "network", "note", "group",
    "external-system",
)
_COMMON_EDGE_KINDS = (
    "flow", "request", "response", "dependency", "data-flow", "async-event",
    "association", "note-link",
)

_TYPES = {
    "flow/basic": DiagramType(
        family="flow",
        type="basic",
        maturity="preview",
        use_when="Showing ordered steps, decisions and branches.",
        do_not_use_when=("The main question is chronological messages between services.",),
        semantic_focus=("one visible primary path", "explicit decisions", "labeled branches"),
        allowed_node_kinds=("start", "end", "process", "decision", "note"),
        allowed_edge_kinds=("flow", "note-link"),
        required_semantics=("stable ids", "explicit edge endpoints"),
        quality_policy={
            "node_overlap": "blocking",
            "edge_routes_through_node": "repairable",
            "primary_path_crossing": "repairable",
            "edge_crossing": "render_cue",
            "note_link_crossing": "render_cue",
            "label_clipped": "repairable",
            "canvas_clipped": "blocking",
            "constraint_unsatisfied": "repairable",
        },
    ),
    "architecture/system-container": DiagramType(
        family="architecture",
        type="system-container",
        maturity="preview",
        use_when="Showing systems, boundaries, deployable services and their dependencies.",
        do_not_use_when=("The main question is chronological message order.",),
        semantic_focus=("boundaries", "responsibilities", "external actors", "labeled flows"),
        allowed_node_kinds=_COMMON_NODE_KINDS,
        allowed_edge_kinds=_COMMON_EDGE_KINDS,
        required_semantics=("stable ids", "explicit relation endpoints"),
        quality_policy={
            "node_overlap": "blocking",
            "edge_routes_through_node": "repairable",
            "primary_path_crossing": "repairable",
            "edge_crossing": "render_cue",
            "cross_group_edge": "accepted",
            "note_link_crossing": "render_cue",
            "label_clipped": "repairable",
            "canvas_clipped": "blocking",
            "constraint_unsatisfied": "repairable",
        },
    ),
}


def list_enabled_types() -> tuple[DiagramType, ...]:
    return tuple(_TYPES[key] for key in sorted(_TYPES))


def get_diagram_type(family: str, diagram_type: str) -> DiagramType | None:
    return _TYPES.get(f"{family}/{diagram_type}")


def base_schema_hash() -> str:
    payload = json.dumps(
        DiagramDocument.model_json_schema(by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()}"
