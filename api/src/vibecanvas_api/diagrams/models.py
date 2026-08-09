"""Strict semantic source models and renderer-neutral Scene IR."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DiagramModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


NodeKind = Literal[
    "start", "end", "process", "decision", "actor", "person", "system",
    "application", "service", "component", "interface", "database", "storage",
    "queue", "event", "cloud", "device", "network", "class", "table",
    "entity", "note", "group", "external-system",
]
EdgeKind = Literal[
    "flow", "request", "response", "dependency", "data-flow", "async-event",
    "association", "inheritance", "composition", "aggregation", "realization",
    "note-link",
]
StyleRole = Literal[
    "neutral", "primary", "secondary", "success", "warning", "danger",
    "actor", "service", "storage", "event", "external", "note",
]


class SemanticPort(DiagramModel):
    id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$",
    )
    label: str | None = Field(default=None, max_length=120)
    side: Literal["AUTO", "NORTH", "EAST", "SOUTH", "WEST"] = "AUTO"
    direction: Literal["in", "out", "inout"] = "inout"


class DiagramIdentity(DiagramModel):
    family: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")
    type: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9-]*$")


class SemanticNode(DiagramModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    kind: NodeKind
    label: str = Field(min_length=1, max_length=240)
    description: str | None = Field(default=None, max_length=2000)
    importance: Literal["primary", "secondary", "supporting"] = "secondary"
    style_role: StyleRole = Field(default="neutral", alias="styleRole")
    ports: list[SemanticPort] = Field(default_factory=list, max_length=32)
    asset_ref: str | None = Field(default=None, alias="assetRef", max_length=128)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticEdge(DiagramModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    source: str = Field(min_length=1, max_length=96)
    target: str = Field(min_length=1, max_length=96)
    kind: EdgeKind = "flow"
    label: str | None = Field(default=None, max_length=240)
    importance: Literal["primary", "secondary", "supporting"] = "secondary"
    source_port: str | None = Field(default=None, alias="sourcePort")
    target_port: str | None = Field(default=None, alias="targetPort")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticGroup(DiagramModel):
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    label: str = Field(min_length=1, max_length=240)
    node_ids: list[str] = Field(default_factory=list, alias="nodeIds", max_length=500)
    parent_id: str | None = Field(default=None, alias="parentId")
    style_role: StyleRole = Field(default="neutral", alias="styleRole")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SemanticModel(DiagramModel):
    nodes: list[SemanticNode] = Field(default_factory=list, max_length=500)
    edges: list[SemanticEdge] = Field(default_factory=list, max_length=1000)
    groups: list[SemanticGroup] = Field(default_factory=list, max_length=100)
    embeds: list[dict[str, Any]] = Field(default_factory=list, max_length=32)
    resources: list[dict[str, Any]] = Field(default_factory=list, max_length=64)


class ElementsConstraint(DiagramModel):
    type: Literal[
        "same-rank",
        "prefer-near",
        "prefer-apart",
        "order",
        "primary-path",
        "increase-gap",
    ]
    elements: list[str] = Field(min_length=2, max_length=100)


class RelativeConstraint(DiagramModel):
    type: Literal["left-of", "right-of", "above", "below"]
    element: str = Field(min_length=1, max_length=96)
    target: str = Field(min_length=1, max_length=96)


class InsideConstraint(DiagramModel):
    type: Literal["inside"]
    element: str = Field(min_length=1, max_length=96)
    container: str = Field(min_length=1, max_length=96)


class RouteConstraint(DiagramModel):
    type: Literal["route-above", "route-below"]
    edge: str = Field(min_length=1, max_length=96)
    element: str = Field(min_length=1, max_length=96)


DiagramConstraint = Annotated[
    ElementsConstraint | RelativeConstraint | InsideConstraint | RouteConstraint,
    Field(discriminator="type"),
]


class PassThroughRule(DiagramModel):
    edge: str = Field(min_length=1, max_length=96)
    element: str = Field(min_length=1, max_length=96)
    render_style: Literal["underlay"] = Field(default="underlay", alias="renderStyle")


class RoutingPolicy(DiagramModel):
    allow_pass_through: list[PassThroughRule] = Field(
        default_factory=list,
        alias="allowPassThrough",
        max_length=100,
    )


class DiagramIntent(DiagramModel):
    direction: Literal["RIGHT", "DOWN", "LEFT", "UP"] = "RIGHT"
    density: Literal["compact", "comfortable", "spacious"] = "comfortable"
    stability: Literal["reflow", "preserve"] = "preserve"
    primary_path: list[str] = Field(default_factory=list, alias="primaryPath", max_length=500)
    constraints: list[DiagramConstraint] = Field(default_factory=list, max_length=500)


class OverridePosition(DiagramModel):
    x: float
    y: float


class OverrideNudge(DiagramModel):
    dx: float = Field(ge=-50, le=50)
    dy: float = Field(ge=-50, le=50)
    unit: Literal["grid"] = "grid"


class ViewOverride(DiagramModel):
    position: OverridePosition | None = None
    nudge: OverrideNudge | None = None
    width: float | None = Field(default=None, ge=80, le=1200)
    height: float | None = Field(default=None, ge=40, le=800)
    pinned: bool = False
    owner: Literal["agent", "user", "compiler"] = "agent"


class DiagramView(DiagramModel):
    layout_mode: Literal["auto", "incremental", "preserve"] = Field(
        default="auto",
        alias="layoutMode",
    )
    overrides: dict[str, ViewOverride] = Field(default_factory=dict)
    frames: list[dict[str, Any]] = Field(default_factory=list, max_length=32)


class DiagramMetadata(DiagramModel):
    created_by: Literal["agent", "user", "import"] = Field(alias="createdBy")
    spec_version: str = Field(alias="specVersion", min_length=1, max_length=64)
    spec_hash: str = Field(alias="specHash", min_length=1, max_length=96)
    compiler_version: str | None = Field(default=None, alias="compilerVersion")
    theme_version: str | None = Field(default=None, alias="themeVersion")
    extra: dict[str, Any] = Field(default_factory=dict)


class DiagramDocument(DiagramModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: str = Field(min_length=1, max_length=96, pattern=r"^[A-Za-z][A-Za-z0-9_.:-]*$")
    title: str = Field(min_length=1, max_length=240)
    diagram: DiagramIdentity
    model: SemanticModel
    intent: DiagramIntent
    view: DiagramView
    routing_policy: RoutingPolicy = Field(default_factory=RoutingPolicy, alias="routingPolicy")
    metadata: DiagramMetadata


class DiagramIssue(DiagramModel):
    issue_id: str = ""
    severity: Literal["error", "warning", "info"]
    disposition: Literal["blocking", "repairable", "render_cue", "accepted"] = "repairable"
    stage: Literal["schema", "semantic", "compile", "visual"]
    code: str
    json_pointer: str = ""
    json_pointers: list[str] = Field(default_factory=list)
    element_id: str | None = None
    element_ids: list[str] = Field(default_factory=list)
    message: str
    suggested_fix: str | None = None
    geometry: dict[str, Any] = Field(default_factory=dict)
    cause: dict[str, Any] = Field(default_factory=dict)
    suggested_operations: list[dict[str, Any]] = Field(default_factory=list)
    auto_fixable: bool = False


class AutoRepairAction(DiagramModel):
    issue_id: str
    code: str
    elements: list[str] = Field(default_factory=list)
    action: str


class AutoRepairReport(DiagramModel):
    attempted: bool = False
    passes: int = Field(default=0, ge=0, le=2)
    resolved: list[AutoRepairAction] = Field(default_factory=list)
    remaining_issue_ids: list[str] = Field(default_factory=list)


class SceneBounds(DiagramModel):
    x: float
    y: float
    width: float
    height: float


class ScenePort(DiagramModel):
    id: str
    label: str | None = None
    side: Literal["NORTH", "EAST", "SOUTH", "WEST"]
    direction: Literal["in", "out", "inout"]
    x: float
    y: float
    source_pointer: str = Field(alias="sourcePointer")


class SceneNode(DiagramModel):
    id: str
    kind: str
    label: str
    label_lines: list[str] = Field(alias="labelLines", min_length=1)
    description: str | None = None
    description_lines: list[str] = Field(
        default_factory=list,
        alias="descriptionLines",
    )
    style_role: str = Field(alias="styleRole")
    importance: str
    asset_ref: str | None = Field(default=None, alias="assetRef")
    ports: list[ScenePort] = Field(default_factory=list)
    bounds: SceneBounds
    source_pointer: str = Field(alias="sourcePointer")
    metadata: dict[str, Any] = Field(default_factory=dict)


class SceneEdge(DiagramModel):
    id: str
    source: str
    target: str
    kind: str
    label: str | None = None
    importance: str
    points: list[dict[str, float]]
    crossings: list[dict[str, Any]] = Field(default_factory=list)
    source_pointer: str = Field(alias="sourcePointer")


class SceneGroup(DiagramModel):
    id: str
    label: str
    style_role: str = Field(alias="styleRole")
    node_ids: list[str] = Field(alias="nodeIds")
    bounds: SceneBounds
    source_pointer: str = Field(alias="sourcePointer")


class DiagramScene(DiagramModel):
    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    diagram_id: str = Field(alias="diagramId")
    title: str
    family: str
    diagram_type: str = Field(alias="diagramType")
    compiler_version: str = Field(alias="compilerVersion")
    theme_version: str = Field(alias="themeVersion")
    bounds: SceneBounds
    nodes: list[SceneNode]
    edges: list[SceneEdge]
    groups: list[SceneGroup]
    issues: list[DiagramIssue] = Field(default_factory=list)
    auto_repair: AutoRepairReport = Field(default_factory=AutoRepairReport)
