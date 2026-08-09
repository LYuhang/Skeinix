"""Schema and cross-reference validation with source-addressable issues."""
from __future__ import annotations

import json
import math
from typing import Any

from pydantic import ValidationError

from .limits import MAX_JSON_DEPTH, MAX_SOURCE_BYTES
from .models import DiagramDocument, DiagramIssue
from .registry import ASSET_CATALOG, REGISTRY_VERSION, get_diagram_type

_EXTERNAL_RESOURCE_KEYS = {
    "url", "uri", "href", "src", "include", "script", "externalurl",
    "external_url", "remoteurl", "remote_url",
}


def _pointer(parts: tuple[Any, ...] | list[Any]) -> str:
    return "".join(f"/{str(part).replace('~', '~0').replace('/', '~1')}" for part in parts)


def _structure_issue(value: Any, path: tuple[Any, ...] = (), depth: int = 0) -> DiagramIssue | None:
    if depth > MAX_JSON_DEPTH:
        return DiagramIssue(
            severity="error", stage="schema", code="source_too_deep",
            json_pointer=_pointer(path),
            message=f"Diagram JSON exceeds the maximum nesting depth of {MAX_JSON_DEPTH}.",
            suggested_fix="Flatten metadata and remove deeply nested embedded data.",
        )
    if isinstance(value, float) and not math.isfinite(value):
        return DiagramIssue(
            severity="error", stage="schema", code="non_finite_number",
            json_pointer=_pointer(path), message="NaN and infinite numbers are not valid diagram values.",
        )
    if isinstance(value, dict):
        for key, child in value.items():
            issue = _structure_issue(child, (*path, key), depth + 1)
            if issue:
                return issue
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issue = _structure_issue(child, (*path, index), depth + 1)
            if issue:
                return issue
    return None


def _external_resource_issue(
    value: Any,
    path: tuple[Any, ...] = (),
) -> DiagramIssue | None:
    """Reject executable/remote references in otherwise extensible metadata."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).replace("-", "_").lower()
            if normalized in _EXTERNAL_RESOURCE_KEYS:
                return DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="external_resource_forbidden",
                    json_pointer=_pointer((*path, key)),
                    message=(
                        "External URL, include and script metadata are disabled "
                        "for VibeDiagram sources."
                    ),
                    suggested_fix=(
                        "Import the resource into VFS and use a registered "
                        "logical resource adapter when one is enabled."
                    ),
                )
            issue = _external_resource_issue(child, (*path, key))
            if issue:
                return issue
    elif isinstance(value, list):
        for index, child in enumerate(value):
            issue = _external_resource_issue(child, (*path, index))
            if issue:
                return issue
    return None


def parse_and_validate(data: bytes | str) -> tuple[DiagramDocument | None, list[DiagramIssue]]:
    raw = data.encode() if isinstance(data, str) else data
    if len(raw) > MAX_SOURCE_BYTES:
        return None, [DiagramIssue(
            severity="error", stage="schema", code="source_too_large",
            message=f"Diagram source exceeds {MAX_SOURCE_BYTES} bytes.",
            suggested_fix="Split the diagram or remove embedded data.",
        )]
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, [DiagramIssue(
            severity="error", stage="schema", code="invalid_json",
            json_pointer="", message=str(exc), suggested_fix="Write valid UTF-8 JSON.",
        )]
    structure_issue = _structure_issue(value)
    if structure_issue:
        return None, [structure_issue]
    try:
        document = DiagramDocument.model_validate(value)
    except ValidationError as exc:
        return None, [DiagramIssue(
            severity="error", stage="schema", code=error["type"],
            json_pointer=_pointer(error["loc"]), message=error["msg"],
            suggested_fix="Match the authoring_schema returned by get_diagram_spec.",
        ) for error in exc.errors(include_url=False)]

    issues: list[DiagramIssue] = []
    external_issue = _external_resource_issue(value)
    if external_issue:
        issues.append(external_issue)
    registered = get_diagram_type(document.diagram.family, document.diagram.type)
    if registered is None:
        issues.append(DiagramIssue(
            severity="error", stage="semantic", code="diagram_type_not_enabled",
            json_pointer="/diagram", message=(
                f"Diagram type '{document.diagram.family}/{document.diagram.type}' is not enabled."
            ), suggested_fix="Choose an enabled type from the /diagram command catalog.",
        ))
        return document, issues
    if document.model.embeds or document.model.resources or document.view.frames:
        issues.append(DiagramIssue(
            severity="error", stage="semantic", code="specialized_frames_not_enabled",
            json_pointer="/model/embeds",
            message="Specialized embeds, companion resources and frames are not enabled in this build.",
            suggested_fix="Use native semantic nodes/edges or remove these fields until an adapter is enabled.",
        ))
    if (
        document.metadata.spec_version != REGISTRY_VERSION
        or document.metadata.spec_hash != registered.spec_hash
    ):
        issues.append(DiagramIssue(
            severity="error", stage="semantic", code="spec_mismatch",
            json_pointer="/metadata/specHash",
            message="Source metadata does not match the selected current diagram spec.",
            suggested_fix="Copy specVersion and specHash from get_diagram_spec.",
        ))

    node_ids: dict[str, int] = {}
    ports_by_node: dict[str, set[str]] = {}
    all_ids: dict[str, str] = {}
    for index, node in enumerate(document.model.nodes):
        if node.id in all_ids:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="duplicate_element_id",
                json_pointer=f"/model/nodes/{index}/id", element_id=node.id,
                message=f"Element id '{node.id}' is already used by {all_ids[node.id]}.",
                suggested_fix="Assign a stable unique ID.",
            ))
        all_ids[node.id] = "another element"
        node_ids[node.id] = index
        port_ids: set[str] = set()
        for port_index, port in enumerate(node.ports):
            if port.id in port_ids:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="duplicate_port_id",
                    json_pointer=(
                        f"/model/nodes/{index}/ports/{port_index}/id"
                    ),
                    element_id=node.id,
                    message=f"Port id '{port.id}' is duplicated on node '{node.id}'.",
                    suggested_fix="Assign a stable unique port ID per node.",
                ))
            port_ids.add(port.id)
        ports_by_node[node.id] = port_ids
        if node.asset_ref:
            asset = ASSET_CATALOG.get(node.asset_ref)
            if asset is None:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="asset_not_found",
                    json_pointer=f"/model/nodes/{index}/assetRef",
                    element_id=node.id,
                    message=f"Asset key '{node.asset_ref}' is not registered.",
                    suggested_fix=(
                        "Use an asset_key returned by search_diagram_assets or "
                        "remove assetRef."
                    ),
                ))
            elif node.kind not in asset["compatible_node_kinds"]:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="asset_node_kind_mismatch",
                    json_pointer=f"/model/nodes/{index}/assetRef",
                    element_id=node.id,
                    message=(
                        f"Asset '{node.asset_ref}' is not compatible with "
                        f"node kind '{node.kind}'."
                    ),
                    suggested_fix="Choose a compatible catalog asset.",
                ))
        if node.kind not in registered.allowed_node_kinds:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="node_kind_not_allowed",
                json_pointer=f"/model/nodes/{index}/kind", element_id=node.id,
                message=f"Node kind '{node.kind}' is not allowed for {registered.key}.",
            ))
    edge_ids: set[str] = set()
    for index, edge in enumerate(document.model.edges):
        if edge.id in all_ids:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="duplicate_element_id",
                json_pointer=f"/model/edges/{index}/id", element_id=edge.id,
                message=f"Element id '{edge.id}' is already used.",
            ))
        all_ids[edge.id] = "another element"
        edge_ids.add(edge.id)
        if edge.source not in node_ids:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="edge_source_not_found",
                json_pointer=f"/model/edges/{index}/source", element_id=edge.id,
                message=f"Source node '{edge.source}' does not exist.",
                suggested_fix="Create the node or use an existing stable node ID.",
            ))
        if edge.target not in node_ids:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="edge_target_not_found",
                json_pointer=f"/model/edges/{index}/target", element_id=edge.id,
                message=f"Target node '{edge.target}' does not exist.",
                suggested_fix="Create the node or use an existing stable node ID.",
            ))
        if edge.source in node_ids and edge.source_port not in {
            None,
            *ports_by_node[edge.source],
        }:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="edge_source_port_not_found",
                json_pointer=f"/model/edges/{index}/sourcePort",
                element_id=edge.id,
                message=(
                    f"Source port '{edge.source_port}' does not exist on "
                    f"node '{edge.source}'."
                ),
                suggested_fix="Use a declared source node port ID or null.",
            ))
        elif edge.source in node_ids and edge.source_port is not None:
            source_node = document.model.nodes[node_ids[edge.source]]
            source_port = next(
                port for port in source_node.ports
                if port.id == edge.source_port
            )
            if source_port.direction == "in":
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="edge_source_port_direction_invalid",
                    json_pointer=f"/model/edges/{index}/sourcePort",
                    element_id=edge.id,
                    message="An input-only port cannot be an edge source.",
                    suggested_fix="Use an out/inout port or null.",
                ))
        if edge.target in node_ids and edge.target_port not in {
            None,
            *ports_by_node[edge.target],
        }:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="edge_target_port_not_found",
                json_pointer=f"/model/edges/{index}/targetPort",
                element_id=edge.id,
                message=(
                    f"Target port '{edge.target_port}' does not exist on "
                    f"node '{edge.target}'."
                ),
                suggested_fix="Use a declared target node port ID or null.",
            ))
        elif edge.target in node_ids and edge.target_port is not None:
            target_node = document.model.nodes[node_ids[edge.target]]
            target_port = next(
                port for port in target_node.ports
                if port.id == edge.target_port
            )
            if target_port.direction == "out":
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="edge_target_port_direction_invalid",
                    json_pointer=f"/model/edges/{index}/targetPort",
                    element_id=edge.id,
                    message="An output-only port cannot be an edge target.",
                    suggested_fix="Use an in/inout port or null.",
                ))
        if edge.kind not in registered.allowed_edge_kinds:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="edge_kind_not_allowed",
                json_pointer=f"/model/edges/{index}/kind", element_id=edge.id,
                message=f"Edge kind '{edge.kind}' is not allowed for {registered.key}.",
            ))
    group_ids = {group.id for group in document.model.groups}
    group_parent: dict[str, str] = {}
    for index, group in enumerate(document.model.groups):
        if group.id in all_ids:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="duplicate_element_id",
                json_pointer=f"/model/groups/{index}/id", element_id=group.id,
                message=f"Element id '{group.id}' is already used.",
            ))
        all_ids[group.id] = "another element"
        if len(set(group.node_ids)) != len(group.node_ids):
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="duplicate_group_member",
                json_pointer=f"/model/groups/{index}/nodeIds",
                element_id=group.id,
                message="A group cannot contain the same node more than once.",
                suggested_fix="Remove duplicate node IDs from this group.",
            ))
        if group.parent_id is not None:
            if group.parent_id not in group_ids:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="group_parent_not_found",
                    json_pointer=f"/model/groups/{index}/parentId",
                    element_id=group.id,
                    message=f"Parent group '{group.parent_id}' does not exist.",
                    suggested_fix="Use an existing group ID or remove parentId.",
                ))
            else:
                group_parent[group.id] = group.parent_id
        for node_id in group.node_ids:
            if node_id not in node_ids:
                issues.append(DiagramIssue(
                    severity="error", stage="semantic", code="group_node_not_found",
                    json_pointer=f"/model/groups/{index}/nodeIds", element_id=group.id,
                    message=f"Group member '{node_id}' does not exist.",
                ))
    for group_id in group_parent:
        seen: set[str] = set()
        cursor: str | None = group_id
        while cursor is not None and cursor in group_parent:
            if cursor in seen:
                group_index = next(
                    index
                    for index, item in enumerate(document.model.groups)
                    if item.id == group_id
                )
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="group_cycle",
                    json_pointer=f"/model/groups/{group_index}/parentId",
                    element_id=group_id,
                    message="Group parent relationships contain a cycle.",
                    suggested_fix="Remove one parentId from the cycle.",
                ))
                break
            seen.add(cursor)
            cursor = group_parent.get(cursor)

    for index, constraint in enumerate(document.intent.constraints):
        pointer = f"/intent/constraints/{index}"
        constraint_type = constraint.type
        if hasattr(constraint, "elements"):
            elements = list(constraint.elements)
            if len(set(elements)) != len(elements):
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="constraint_duplicate_element",
                    json_pointer=f"{pointer}/elements",
                    message=(
                        f"Constraint '{constraint_type}' contains duplicate "
                        "element IDs."
                    ),
                    suggested_fix="Keep each constrained node ID once.",
                ))
            for element_index, element_id in enumerate(elements):
                if element_id not in node_ids:
                    issues.append(DiagramIssue(
                        severity="error",
                        stage="semantic",
                        code="constraint_element_not_found",
                        json_pointer=(
                            f"{pointer}/elements/{element_index}"
                        ),
                        element_id=element_id,
                        message=(
                            f"Constraint node '{element_id}' does not exist."
                        ),
                        suggested_fix="Use an existing stable node ID.",
                    ))
        elif constraint_type == "inside":
            if constraint.element not in node_ids:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="constraint_element_not_found",
                    json_pointer=f"{pointer}/element",
                    element_id=constraint.element,
                    message=(
                        f"Constraint node '{constraint.element}' does not exist."
                    ),
                ))
            if constraint.container not in group_ids:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="constraint_container_not_found",
                    json_pointer=f"{pointer}/container",
                    element_id=constraint.container,
                    message=(
                        f"Constraint group '{constraint.container}' does not exist."
                    ),
                    suggested_fix="Use an existing semantic group ID.",
                ))
        elif constraint_type in {"route-above", "route-below"}:
            if constraint.edge not in edge_ids:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="constraint_edge_not_found",
                    json_pointer=f"{pointer}/edge",
                    element_id=constraint.edge,
                    message=f"Constraint edge '{constraint.edge}' does not exist.",
                    suggested_fix="Use an existing stable edge ID.",
                ))
            if constraint.element not in node_ids:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="constraint_element_not_found",
                    json_pointer=f"{pointer}/element",
                    element_id=constraint.element,
                    message=(
                        f"Constraint node '{constraint.element}' does not exist."
                    ),
                ))
        else:
            if constraint.element == constraint.target:
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="constraint_self_reference",
                    json_pointer=f"{pointer}/target",
                    element_id=constraint.element,
                    message="A relative constraint cannot target the same node.",
                ))
            for field in ("element", "target"):
                element_id = getattr(constraint, field)
                if element_id not in node_ids:
                    issues.append(DiagramIssue(
                        severity="error",
                        stage="semantic",
                        code="constraint_element_not_found",
                        json_pointer=f"{pointer}/{field}",
                        element_id=element_id,
                        message=(
                            f"Constraint node '{element_id}' does not exist."
                        ),
                        suggested_fix="Use an existing stable node ID.",
                    ))

    if document.view.layout_mode == "preserve":
        for index, node in enumerate(document.model.nodes):
            override = document.view.overrides.get(node.id)
            if (
                override is None
                or override.position is None
                or override.position.x is None
                or override.position.y is None
            ):
                issues.append(DiagramIssue(
                    severity="error",
                    stage="semantic",
                    code="preserve_position_missing",
                    json_pointer=f"/model/nodes/{index}/id",
                    element_id=node.id,
                    message=(
                        "Preserve layout requires an x/y override for every node."
                    ),
                    suggested_fix=(
                        "Provide retained positions or use incremental layout."
                    ),
                ))
    for index, node_id in enumerate(document.intent.primary_path):
        if node_id not in node_ids:
            issues.append(DiagramIssue(
                severity="error", stage="semantic", code="primary_path_node_not_found",
                json_pointer=f"/intent/primaryPath/{index}", element_id=node_id,
                message=f"Primary path node '{node_id}' does not exist.",
            ))
    for node_id in document.view.overrides:
        if node_id not in node_ids:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="override_node_not_found",
                json_pointer=(
                    "/view/overrides/"
                    + node_id.replace("~", "~0").replace("/", "~1")
                ),
                element_id=node_id,
                message=f"View override node '{node_id}' does not exist.",
                suggested_fix="Remove the override or restore the semantic node.",
            ))
    pass_through_pairs: set[tuple[str, str]] = set()
    edges_by_id = {edge.id: edge for edge in document.model.edges}
    for index, rule in enumerate(document.routing_policy.allow_pass_through):
        pointer = f"/routingPolicy/allowPassThrough/{index}"
        pair = (rule.edge, rule.element)
        if pair in pass_through_pairs:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="duplicate_pass_through_rule",
                json_pointer=pointer,
                element_id=rule.edge,
                message="A pass-through edge/element pair may be declared once.",
            ))
        pass_through_pairs.add(pair)
        edge = edges_by_id.get(rule.edge)
        if edge is None:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="pass_through_edge_not_found",
                json_pointer=f"{pointer}/edge",
                element_id=rule.edge,
                message=f"Pass-through edge '{rule.edge}' does not exist.",
            ))
        if rule.element not in node_ids:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="pass_through_element_not_found",
                json_pointer=f"{pointer}/element",
                element_id=rule.element,
                message=f"Pass-through node '{rule.element}' does not exist.",
            ))
        elif edge is not None and rule.element in {edge.source, edge.target}:
            issues.append(DiagramIssue(
                severity="error",
                stage="semantic",
                code="pass_through_endpoint_invalid",
                json_pointer=f"{pointer}/element",
                element_id=rule.element,
                message=(
                    "Pass-through applies only to an unrelated node, not an "
                    "edge source or target."
                ),
            ))
    return document, issues
