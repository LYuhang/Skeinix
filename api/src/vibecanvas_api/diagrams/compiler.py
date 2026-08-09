"""Deterministic graph layout and Scene IR composition.

The semantic source never owns ordinary node coordinates. This compiler keeps
the layout deterministic and renderer-neutral so Preview, Review and Export can
consume the exact same bounds and routes.
"""
from __future__ import annotations

import json
import math
import unicodedata
from collections import defaultdict, deque
from itertools import pairwise

from .elk_layout import run_elk_layout
from .limits import (
    COMPILE_TIMEOUT_SECONDS,
    MAX_SCENE_BYTES,
    DiagramLimitError,
    check_canvas_extent,
    check_deadline,
    deadline_after,
)
from .models import (
    AutoRepairAction,
    AutoRepairReport,
    DiagramDocument,
    DiagramIssue,
    DiagramScene,
    SceneBounds,
    SceneEdge,
    SceneGroup,
    SceneNode,
    ScenePort,
    SemanticNode,
)
from .registry import COMPILER_VERSION, THEME_VERSION, get_diagram_type


def _bounds_overlap(left: SceneBounds, right: SceneBounds) -> bool:
    return bool(
        left.x < right.x + right.width
        and left.x + left.width > right.x
        and left.y < right.y + right.height
        and left.y + left.height > right.y
    )


def _collision_pairs(bounds_by_id: dict[str, SceneBounds]) -> set[tuple[str, str]]:
    node_ids = sorted(bounds_by_id)
    return {
        (left_id, right_id)
        for index, left_id in enumerate(node_ids)
        for right_id in node_ids[index + 1:]
        if _bounds_overlap(bounds_by_id[left_id], bounds_by_id[right_id])
    }


def _elk_graph(
    document: DiagramDocument,
    bounds_by_id: dict[str, SceneBounds],
) -> dict[str, object]:
    density = document.intent.density
    node_spacing = {"compact": 72, "comfortable": 96, "spacious": 128}[
        density
    ]
    layer_spacing = {"compact": 64, "comfortable": 92, "spacious": 124}[
        density
    ]
    options = {
        "elk.algorithm": "layered",
        "elk.direction": document.intent.direction,
        "elk.edgeRouting": "ORTHOGONAL",
        "elk.spacing.nodeNode": str(node_spacing),
        "elk.layered.spacing.nodeNodeBetweenLayers": str(layer_spacing),
        "elk.layered.spacing.edgeNodeBetweenLayers": "36",
        "elk.layered.crossingMinimization.forceNodeModelOrder": "true",
        "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
        "elk.layered.cycleBreaking.strategy": "GREEDY",
        "elk.layered.nodePlacement.favorStraightEdges": "true",
        "elk.randomSeed": "1",
        "elk.padding": "[top=12,left=12,bottom=12,right=12]",
    }
    if document.view.layout_mode == "incremental":
        options.update({
            "elk.interactive": "true",
            "elk.layered.interactiveLayout": "true",
            "elk.layered.crossingMinimization.semiInteractive": "true",
            "elk.layered.nodePlacement.strategy": "INTERACTIVE",
        })

    children: list[dict[str, object]] = []
    for node in document.model.nodes:
        bounds = bounds_by_id[node.id]
        child: dict[str, object] = {
            "id": node.id,
            "width": bounds.width,
            "height": bounds.height,
        }
        override = document.view.overrides.get(node.id)
        if (
            document.view.layout_mode == "incremental"
            and override is not None
            and override.position is not None
        ):
            child["x"] = bounds.x
            child["y"] = bounds.y
        children.append(child)

    edges: list[dict[str, object]] = [
        {
            "id": edge.id,
            "sources": [edge.source],
            "targets": [edge.target],
        }
        for edge in document.model.edges
    ]
    synthetic_index = 0

    def add_order(source: str, target: str) -> None:
        nonlocal synthetic_index
        if source == target:
            return
        edges.append({
            "id": f"__constraint__{synthetic_index}",
            "sources": [source],
            "targets": [target],
        })
        synthetic_index += 1

    for source, target in zip(
        document.intent.primary_path,
        document.intent.primary_path[1:],
    ):
        add_order(source, target)
    for constraint in document.intent.constraints:
        if constraint.type in {"order", "primary-path"}:
            for source, target in zip(
                constraint.elements,
                constraint.elements[1:],
            ):
                add_order(source, target)
        elif constraint.type == "left-of":
            add_order(constraint.element, constraint.target)
        elif constraint.type == "right-of":
            add_order(constraint.target, constraint.element)
        elif constraint.type == "above" and document.intent.direction == "DOWN":
            add_order(constraint.element, constraint.target)
        elif constraint.type == "below" and document.intent.direction == "DOWN":
            add_order(constraint.target, constraint.element)

    return {
        "id": document.id,
        "layoutOptions": options,
        "children": children,
        "edges": edges,
    }


def _layout_with_elk(
    document: DiagramDocument,
    bounds_by_id: dict[str, SceneBounds],
) -> dict[str, list[dict[str, float]]]:
    result = run_elk_layout(_elk_graph(document, bounds_by_id))
    children = result.get("children")
    edges = result.get("edges")
    if not isinstance(children, list) or not isinstance(edges, list):
        raise DiagramLimitError(
            "layout_engine_invalid_output",
            "ELK layout omitted nodes or edges.",
        )

    expected_ids = set(bounds_by_id)
    seen_ids: set[str] = set()
    for child in children:
        if not isinstance(child, dict) or child.get("id") not in expected_ids:
            raise DiagramLimitError(
                "layout_engine_invalid_output",
                "ELK layout returned an unknown node.",
            )
        node_id = str(child["id"])
        try:
            x = round(float(child["x"]) + 72, 2)
            y = round(float(child["y"]) + 72, 2)
        except (KeyError, TypeError, ValueError) as exc:
            raise DiagramLimitError(
                "layout_engine_invalid_output",
                "ELK layout returned an invalid node position.",
            ) from exc
        current = bounds_by_id[node_id]
        bounds_by_id[node_id] = current.model_copy(update={"x": x, "y": y})
        seen_ids.add(node_id)
    if seen_ids != expected_ids:
        raise DiagramLimitError(
            "layout_engine_invalid_output",
            "ELK layout omitted one or more nodes.",
        )

    # Every retained incremental position outranks automatic layout.  The
    # inspect contract gives these coordinates back to the Agent specifically
    # to preserve the user's mental map; only newly added elements are placed
    # by ELK. User pins and explicit nudges remain fixed in every mode.
    for node_id, override in document.view.overrides.items():
        if override.position is None:
            continue
        should_fix = document.view.layout_mode == "incremental"
        should_fix = should_fix or (override.owner == "user" and override.pinned)
        should_fix = should_fix or override.nudge is not None
        if not should_fix:
            continue
        x = override.position.x
        y = override.position.y
        if override.nudge is not None:
            x += override.nudge.dx * 24
            y += override.nudge.dy * 24
        current = bounds_by_id[node_id]
        bounds_by_id[node_id] = current.model_copy(update={
            "x": round(x, 2),
            "y": round(y, 2),
        })

    routes: dict[str, list[dict[str, float]]] = {}
    model_edge_ids = {edge.id for edge in document.model.edges}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("id") not in model_edge_ids:
            continue
        sections = edge.get("sections")
        if not isinstance(sections, list) or not sections:
            continue
        points: list[dict[str, float]] = []
        for section in sections:
            if not isinstance(section, dict):
                continue
            sequence = [
                section.get("startPoint"),
                *(section.get("bendPoints") or []),
                section.get("endPoint"),
            ]
            for point in sequence:
                if not isinstance(point, dict):
                    continue
                try:
                    normalized = {
                        "x": round(float(point["x"]) + 72, 2),
                        "y": round(float(point["y"]) + 72, 2),
                    }
                except (KeyError, TypeError, ValueError) as exc:
                    raise DiagramLimitError(
                        "layout_engine_invalid_output",
                        "ELK layout returned an invalid edge point.",
                    ) from exc
                if not points or points[-1] != normalized:
                    points.append(normalized)
        if len(points) >= 2:
            routes[str(edge["id"])] = points
    return routes


def _text_units(value: str) -> int:
    return sum(
        2
        if unicodedata.east_asian_width(character) in {"W", "F"}
        or unicodedata.category(character) == "So"
        else 1
        for character in value
    )


def _wrap_text(value: str, max_units: int, max_lines: int) -> list[str]:
    words = value.split()
    tokens = words if len(words) > 1 else list(value)
    lines: list[str] = []
    current = ""
    separator = " " if len(words) > 1 else ""
    for token in tokens:
        candidate = token if not current else f"{current}{separator}{token}"
        if current and _text_units(candidate) > max_units:
            lines.append(current)
            current = token
            if len(lines) == max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    consumed = separator.join(lines)
    if _text_units(consumed) < _text_units(value) and lines:
        tail = lines[-1]
        while tail and _text_units(f"{tail}…") > max_units:
            tail = tail[:-1]
        lines[-1] = f"{tail}…"
    return lines or [value]


def _node_size(
    label: str,
    description: str | None,
    density: str,
) -> tuple[float, float, list[str], list[str]]:
    width = min(320, max(168, 72 + min(30, _text_units(label)) * 7.2))
    label_lines = _wrap_text(label, max(12, int((width - 48) / 7.2)), 3)
    description_lines = (
        []
        if not description
        else _wrap_text(description, max(18, int((width - 24) / 6.2)), 3)
    )
    height = 54 + max(0, len(label_lines) - 1) * 18 + len(description_lines) * 16
    scale = {"compact": 0.9, "comfortable": 1.0, "spacious": 1.12}[density]
    return (
        round(width * scale, 2),
        round(height * scale, 2),
        label_lines,
        description_lines,
    )


def _ranks(document: DiagramDocument) -> dict[str, int]:
    ids = [node.id for node in document.model.nodes]
    incoming = {node_id: 0 for node_id in ids}
    outgoing: dict[str, list[str]] = defaultdict(list)
    for edge in document.model.edges:
        if edge.source in incoming and edge.target in incoming and edge.source != edge.target:
            outgoing[edge.source].append(edge.target)
            incoming[edge.target] += 1
    queue = deque(sorted(node_id for node_id, count in incoming.items() if count == 0))
    rank = {node_id: 0 for node_id in ids}
    visited: set[str] = set()
    while queue:
        source = queue.popleft()
        visited.add(source)
        for target in sorted(outgoing[source]):
            rank[target] = max(rank[target], rank[source] + 1)
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    # Cycles are legal for architecture diagrams. Put unresolved nodes into a
    # stable trailing rank rather than depending on traversal order.
    trailing = max(rank.values(), default=0) + (1 if visited else 0)
    for node_id in sorted(set(ids) - visited):
        rank[node_id] = trailing
    return rank


def _automatic_port_side(direction: str, port_direction: str) -> str:
    outgoing = {
        "RIGHT": "EAST",
        "LEFT": "WEST",
        "DOWN": "SOUTH",
        "UP": "NORTH",
    }[direction]
    incoming = {
        "RIGHT": "WEST",
        "LEFT": "EAST",
        "DOWN": "NORTH",
        "UP": "SOUTH",
    }[direction]
    return incoming if port_direction == "in" else outgoing


def _scene_ports(
    node,
    bounds: SceneBounds,
    direction: str,
    source_pointer: str,
) -> list[ScenePort]:
    resolved = [
        (
            port,
            port.side
            if port.side != "AUTO"
            else _automatic_port_side(direction, port.direction),
        )
        for port in node.ports
    ]
    counts: dict[str, int] = defaultdict(int)
    for _, side in resolved:
        counts[side] += 1
    indexes: dict[str, int] = defaultdict(int)
    result: list[ScenePort] = []
    for port_index, (port, side) in enumerate(resolved):
        indexes[side] += 1
        fraction = indexes[side] / (counts[side] + 1)
        if side == "NORTH":
            x, y = bounds.x + bounds.width * fraction, bounds.y
        elif side == "SOUTH":
            x, y = (
                bounds.x + bounds.width * fraction,
                bounds.y + bounds.height,
            )
        elif side == "WEST":
            x, y = bounds.x, bounds.y + bounds.height * fraction
        else:
            x, y = (
                bounds.x + bounds.width,
                bounds.y + bounds.height * fraction,
            )
        result.append(ScenePort(
            id=port.id,
            label=port.label,
            side=side,
            direction=port.direction,
            x=round(x, 2),
            y=round(y, 2),
            sourcePointer=f"{source_pointer}/ports/{port_index}",
        ))
    return result


def _position_is_fixed(
    document: DiagramDocument,
    node_id: str,
    *,
    soft: bool,
) -> bool:
    if document.view.layout_mode == "preserve":
        return True
    override = document.view.overrides.get(node_id)
    if override is None:
        return False
    if override.owner == "user" and override.pinned:
        return True
    # Compiler-owned positions returned by inspect are hard constraints during
    # incremental modification. Moving them would make inserting one new node
    # unexpectedly rearrange unrelated content and break the mental map.
    return bool(
        override.position is not None
        and document.view.layout_mode == "incremental"
    )


def _apply_position_constraints(
    document: DiagramDocument,
    bounds_by_id: dict[str, SceneBounds],
    *,
    major_gap: float,
) -> None:
    """Apply semantic constraints without moving user-owned/preserved nodes."""
    horizontal = document.intent.direction in {"RIGHT", "LEFT"}

    def move(
        node_id: str,
        *,
        x: float | None = None,
        y: float | None = None,
        soft: bool = False,
    ) -> None:
        if _position_is_fixed(document, node_id, soft=soft):
            return
        current = bounds_by_id[node_id]
        bounds_by_id[node_id] = current.model_copy(update={
            "x": round(current.x if x is None else x, 2),
            "y": round(current.y if y is None else y, 2),
        })

    # Two deterministic passes let short relative chains settle without an
    # unbounded constraint solver or order-dependent iteration.
    for _pass in range(2):
        for constraint in document.intent.constraints:
            kind = constraint.type
            if kind == "same-rank":
                elements = list(constraint.elements)
                fixed = [
                    element
                    for element in elements
                    if _position_is_fixed(document, element, soft=False)
                ]
                anchor = bounds_by_id[(fixed or elements)[0]]
                for element in elements:
                    if horizontal:
                        move(element, x=anchor.x)
                    else:
                        move(element, y=anchor.y)
            elif kind in {"left-of", "right-of", "above", "below"}:
                current = bounds_by_id[constraint.element]
                target = bounds_by_id[constraint.target]
                gap = 48.0
                if kind == "right-of":
                    move(
                        constraint.element,
                        x=max(current.x, target.x + target.width + gap),
                    )
                elif kind == "left-of":
                    move(
                        constraint.element,
                        x=min(current.x, target.x - current.width - gap),
                    )
                elif kind == "below":
                    move(
                        constraint.element,
                        y=max(current.y, target.y + target.height + gap),
                    )
                else:
                    move(
                        constraint.element,
                        y=min(current.y, target.y - current.height - gap),
                    )
            elif kind in {"order", "primary-path"}:
                previous_id = constraint.elements[0]
                for element in constraint.elements[1:]:
                    previous = bounds_by_id[previous_id]
                    current = bounds_by_id[element]
                    if document.intent.direction == "RIGHT":
                        move(
                            element,
                            x=max(current.x, previous.x + previous.width + 48),
                        )
                    elif document.intent.direction == "LEFT":
                        move(
                            element,
                            x=min(current.x, previous.x - current.width - 48),
                        )
                    elif document.intent.direction == "DOWN":
                        move(
                            element,
                            y=max(current.y, previous.y + previous.height + 48),
                        )
                    else:
                        move(
                            element,
                            y=min(current.y, previous.y - current.height - 48),
                        )
                    previous_id = element

    for constraint in document.intent.constraints:
        kind = constraint.type
        if kind not in {"prefer-near", "prefer-apart", "increase-gap"}:
            continue
        anchor_id = constraint.elements[0]
        anchor = bounds_by_id[anchor_id]
        anchor_center = (
            anchor.x + anchor.width / 2,
            anchor.y + anchor.height / 2,
        )
        minimum = major_gap * (1.5 if kind == "increase-gap" else 1.0)
        maximum = major_gap * 1.25
        for index, element in enumerate(constraint.elements[1:], start=1):
            current = bounds_by_id[element]
            current_center = (
                current.x + current.width / 2,
                current.y + current.height / 2,
            )
            dx = current_center[0] - anchor_center[0]
            dy = current_center[1] - anchor_center[1]
            distance = math.hypot(dx, dy)
            if kind == "prefer-near" and distance > maximum:
                ratio = maximum / max(distance, 1)
                move(
                    element,
                    x=anchor_center[0] + dx * ratio - current.width / 2,
                    y=anchor_center[1] + dy * ratio - current.height / 2,
                    soft=True,
                )
            elif kind != "prefer-near" and distance < minimum:
                if distance < 1:
                    dx, dy = (0.0, 1.0 if index % 2 else -1.0)
                    distance = 1.0
                ratio = minimum / distance
                move(
                    element,
                    x=anchor_center[0] + dx * ratio - current.width / 2,
                    y=anchor_center[1] + dy * ratio - current.height / 2,
                    soft=True,
                )


def _dedupe_route(points: list[dict[str, float]]) -> list[dict[str, float]]:
    result: list[dict[str, float]] = []
    for point in points:
        normalized = {"x": round(point["x"], 2), "y": round(point["y"], 2)}
        if result and result[-1] == normalized:
            continue
        result.append(normalized)
    return result


def _resolve_node_collisions(
    document: DiagramDocument,
    bounds_by_id: dict[str, SceneBounds],
) -> None:
    """Allocate a deterministic minor-axis lane after intent constraints.

    Relative constraints can deliberately move several nodes onto the same
    major-axis rank. That must not leave identical rectangles behind. Fixed
    user/preserve positions are placed first and never moved; ordinary nodes
    shift only along the minor axis, preserving rank and reading direction.
    Remaining fixed/fixed conflicts are surfaced by Visual Lint.
    """
    horizontal = document.intent.direction in {"RIGHT", "LEFT"}
    gap = 32.0

    ordered = sorted(
        bounds_by_id,
        key=lambda node_id: (
            not _position_is_fixed(document, node_id, soft=True),
            bounds_by_id[node_id].x if horizontal else bounds_by_id[node_id].y,
            bounds_by_id[node_id].y if horizontal else bounds_by_id[node_id].x,
            node_id,
        ),
    )
    placed: list[str] = []
    for node_id in ordered:
        if _position_is_fixed(document, node_id, soft=True):
            placed.append(node_id)
            continue
        current = bounds_by_id[node_id]
        # Each pass moves beyond at least one conflicting rectangle, so this
        # converges in at most the number of already placed nodes.
        for _attempt in range(len(placed) + 1):
            conflicts = [
                bounds_by_id[other_id]
                for other_id in placed
                if _bounds_overlap(current, bounds_by_id[other_id])
            ]
            if not conflicts:
                break
            if horizontal:
                current = current.model_copy(update={
                    "y": round(max(
                        item.y + item.height for item in conflicts
                    ) + gap, 2),
                })
            else:
                current = current.model_copy(update={
                    "x": round(max(
                        item.x + item.width for item in conflicts
                    ) + gap, 2),
                })
            bounds_by_id[node_id] = current
        placed.append(node_id)


def _route_around_nodes(
    points: list[dict[str, float]],
    *,
    source_id: str,
    target_id: str,
    bounds_by_id: dict[str, SceneBounds],
    horizontal: bool,
    forced_vertical_side: str | None = None,
    ignored_blocker_ids: set[str] | None = None,
) -> list[dict[str, float]]:
    ignored = ignored_blocker_ids or set()
    blocker_items = [
        (node_id, bounds)
        for node_id, bounds in bounds_by_id.items()
        if node_id not in {source_id, target_id}
        and node_id not in ignored
    ]
    blockers = [
        bounds
        for _, bounds in blocker_items
        if any(
            _segment_intersects_bounds(start, end, bounds)
            for start, end in pairwise(points)
        )
    ]
    if not blockers and forced_vertical_side is None:
        return _dedupe_route(points)
    start, end = points[0], points[-1]
    all_bounds = list(bounds_by_id.values())
    left = min(item.x for item in all_bounds) - 36
    right = max(item.x + item.width for item in all_bounds) + 36
    above = min(item.y for item in all_bounds) - 36
    below = max(item.y + item.height for item in all_bounds) + 36

    def clear(candidate: list[dict[str, float]]) -> bool:
        return not any(
            _segment_intersects_bounds(segment_start, segment_end, bounds)
            for _, bounds in blocker_items
            for segment_start, segment_end in pairwise(candidate)
        )

    def route_length(candidate: list[dict[str, float]]) -> float:
        return sum(
            abs(segment_end["x"] - segment_start["x"])
            + abs(segment_end["y"] - segment_start["y"])
            for segment_start, segment_end in pairwise(candidate)
        )

    if horizontal or forced_vertical_side in {"above", "below"}:
        lane_candidates = (
            [above if forced_vertical_side == "above" else below]
            if forced_vertical_side in {"above", "below"}
            else [above, below]
        )
        candidates = [
            _dedupe_route([
                start,
                {"x": start_connector, "y": start["y"]},
                {"x": start_connector, "y": lane_y},
                {"x": end_connector, "y": lane_y},
                {"x": end_connector, "y": end["y"]},
                end,
            ])
            for lane_y in lane_candidates
            for start_connector in (start["x"], left, right)
            for end_connector in (end["x"], left, right)
            if start_connector != end_connector
        ]
        source_bounds = bounds_by_id[source_id]
        target_bounds = bounds_by_id[target_id]
        # Dense ranks can block both direct vertical connector legs. Escape a
        # short distance above/below each endpoint first, then enter an outer
        # corridor. Source/target bounds are intentionally excluded blockers,
        # so this remains valid for any semantic node size.
        candidates.extend(
            _dedupe_route([
                start,
                {"x": start["x"], "y": start_escape_y},
                {"x": start_connector, "y": start_escape_y},
                {"x": start_connector, "y": lane_y},
                {"x": end_connector, "y": lane_y},
                {"x": end_connector, "y": end_escape_y},
                {"x": end["x"], "y": end_escape_y},
                end,
            ])
            for lane_y in lane_candidates
            for start_escape_y in (
                start["y"],
                source_bounds.y - 12,
                source_bounds.y + source_bounds.height + 12,
            )
            for end_escape_y in (
                end["y"],
                target_bounds.y - 12,
                target_bounds.y + target_bounds.height + 12,
            )
            for start_connector in (left, right)
            for end_connector in (left, right)
            if start_connector != end_connector
        )
    else:
        lane_candidates = [left, right]
        candidates = [
            _dedupe_route([
                start,
                {"x": start["x"], "y": start_connector},
                {"x": lane_x, "y": start_connector},
                {"x": lane_x, "y": end_connector},
                {"x": end["x"], "y": end_connector},
                end,
            ])
            for lane_x in lane_candidates
            for start_connector in (start["y"], above, below)
            for end_connector in (end["y"], above, below)
            if start_connector != end_connector
        ]
        source_bounds = bounds_by_id[source_id]
        target_bounds = bounds_by_id[target_id]
        candidates.extend(
            _dedupe_route([
                start,
                {"x": start_escape_x, "y": start["y"]},
                {"x": start_escape_x, "y": start_connector},
                {"x": lane_x, "y": start_connector},
                {"x": lane_x, "y": end_connector},
                {"x": end_escape_x, "y": end_connector},
                {"x": end_escape_x, "y": end["y"]},
                end,
            ])
            for lane_x in lane_candidates
            for start_escape_x in (
                start["x"],
                source_bounds.x - 12,
                source_bounds.x + source_bounds.width + 12,
            )
            for end_escape_x in (
                end["x"],
                target_bounds.x - 12,
                target_bounds.x + target_bounds.width + 12,
            )
            for start_connector in (above, below)
            for end_connector in (above, below)
            if start_connector != end_connector
        )

    valid_candidates = [candidate for candidate in candidates if clear(candidate)]
    if valid_candidates:
        return min(
            valid_candidates,
            key=lambda candidate: (
                route_length(candidate),
                tuple((point["x"], point["y"]) for point in candidate),
            ),
        )

    if forced_vertical_side in {"above", "below"}:
        lane_y = above if forced_vertical_side == "above" else below
        return _dedupe_route([
            start,
            {"x": start["x"], "y": lane_y},
            {"x": end["x"], "y": lane_y},
            end,
        ])
    if horizontal:
        lane_y = min(
            (above, below),
            key=lambda candidate: (
                abs(start["y"] - candidate) + abs(end["y"] - candidate),
                candidate,
            ),
        )
        return _dedupe_route([
            start,
            {"x": start["x"], "y": lane_y},
            {"x": end["x"], "y": lane_y},
            end,
        ])
    lane_x = min(
        (left, right),
        key=lambda candidate: (
            abs(start["x"] - candidate) + abs(end["x"] - candidate),
            candidate,
        ),
    )
    return _dedupe_route([
        start,
        {"x": lane_x, "y": start["y"]},
        {"x": lane_x, "y": end["y"]},
        end,
    ])


def compile_diagram(
    document: DiagramDocument,
    *,
    timeout_seconds: float = COMPILE_TIMEOUT_SECONDS,
) -> DiagramScene:
    deadline = deadline_after(timeout_seconds)
    check_deadline(deadline, operation="compile")
    rank = _ranks(document)
    by_rank: dict[int, list[str]] = defaultdict(list)
    for node in document.model.nodes:
        by_rank[rank[node.id]].append(node.id)
    for values in by_rank.values():
        values.sort()

    direction = document.intent.direction
    horizontal = direction in {"RIGHT", "LEFT"}
    major_gap = {"compact": 210, "comfortable": 260, "spacious": 320}[document.intent.density]
    minor_gap = {"compact": 120, "comfortable": 150, "spacious": 190}[document.intent.density]
    node_by_id = {node.id: node for node in document.model.nodes}
    bounds_by_id: dict[str, SceneBounds] = {}
    node_render_data: list[
        tuple[SemanticNode, list[str], list[str], str]
    ] = []
    max_rank = max(by_rank, default=0)
    for rank_index in sorted(by_rank):
        check_deadline(deadline, operation="compile")
        values = by_rank[rank_index]
        for lane, node_id in enumerate(values):
            node = node_by_id[node_id]
            width, height, label_lines, description_lines = _node_size(
                node.label,
                node.description,
                document.intent.density,
            )
            major = rank_index if direction in {"RIGHT", "DOWN"} else max_rank - rank_index
            x = 72 + (major * major_gap if horizontal else lane * minor_gap)
            y = 72 + (lane * minor_gap if horizontal else major * major_gap)
            override = document.view.overrides.get(node.id)
            keeps_position = (
                override is not None
                and override.position is not None
                and override.position.x is not None
                and override.position.y is not None
                and (
                    override.pinned
                    or document.view.layout_mode in {"incremental", "preserve"}
                )
            )
            if keeps_position:
                x, y = override.position.x, override.position.y
            if (
                override
                and override.nudge
                and document.view.layout_mode == "incremental"
            ):
                x += override.nudge.dx * 24
                y += override.nudge.dy * 24
            if override and override.width is not None:
                width = override.width
            if override and override.height is not None:
                height = override.height
            bounds = SceneBounds(x=x, y=y, width=width, height=height)
            bounds_by_id[node.id] = bounds
            source_pointer = f"/model/nodes/{document.model.nodes.index(node)}"
            node_render_data.append((
                node,
                label_lines,
                description_lines,
                source_pointer,
            ))

    elk_routes: dict[str, list[dict[str, float]]] = {}
    if document.view.layout_mode != "preserve":
        elk_routes = _layout_with_elk(document, bounds_by_id)
    before_constraints = {
        node_id: bounds.model_dump()
        for node_id, bounds in bounds_by_id.items()
    }
    _apply_position_constraints(
        document,
        bounds_by_id,
        major_gap=major_gap,
    )
    collisions_before_repair = _collision_pairs(bounds_by_id)
    _resolve_node_collisions(document, bounds_by_id)
    # A second bounded pass settles collisions introduced by a first-pass lane
    # shift without turning layout into an unbounded search.
    _resolve_node_collisions(document, bounds_by_id)
    collisions_after_repair = _collision_pairs(bounds_by_id)
    layout_adjusted_after_elk = any(
        bounds_by_id[node_id].model_dump() != before_constraints[node_id]
        for node_id in bounds_by_id
    )
    has_retained_post_layout_position = any(
        override.position is not None
        and (
            document.view.layout_mode == "incremental"
            or (override.owner == "user" and override.pinned)
            or override.nudge is not None
        )
        for override in document.view.overrides.values()
    )
    use_elk_routes = bool(elk_routes) and not (
        layout_adjusted_after_elk or has_retained_post_layout_position
    )
    scene_nodes: list[SceneNode] = []
    for node, label_lines, description_lines, source_pointer in node_render_data:
        bounds = bounds_by_id[node.id]
        scene_nodes.append(SceneNode(
            id=node.id, kind=node.kind, label=node.label,
            labelLines=label_lines,
            description=node.description, styleRole=node.style_role,
            descriptionLines=description_lines,
            importance=node.importance, bounds=bounds,
            assetRef=node.asset_ref,
            ports=_scene_ports(
                node,
                bounds,
                direction,
                source_pointer,
            ),
            sourcePointer=source_pointer,
            metadata=node.metadata,
        ))

    scene_edges: list[SceneEdge] = []
    ports_by_node = {
        node.id: {port.id: port for port in node.ports}
        for node in scene_nodes
    }
    route_preferences = {
        constraint.edge: (
            "above" if constraint.type == "route-above" else "below"
        )
        for constraint in document.intent.constraints
        if constraint.type in {"route-above", "route-below"}
    }
    pass_through_by_edge: dict[str, set[str]] = defaultdict(set)
    for rule in document.routing_policy.allow_pass_through:
        pass_through_by_edge[rule.edge].add(rule.element)
    for index, edge in enumerate(document.model.edges):
        if index % 32 == 0:
            check_deadline(deadline, operation="compile")
        source = bounds_by_id[edge.source]
        target = bounds_by_id[edge.target]
        source_port = ports_by_node[edge.source].get(edge.source_port or "")
        target_port = ports_by_node[edge.target].get(edge.target_port or "")
        elk_points = elk_routes.get(edge.id)
        if (
            use_elk_routes
            and elk_points is not None
            and source_port is None
            and target_port is None
            and edge.id not in route_preferences
        ):
            points = elk_points
        elif direction == "RIGHT":
            start = {"x": source.x + source.width, "y": source.y + source.height / 2}
            end = {"x": target.x, "y": target.y + target.height / 2}
            middle = (start["x"] + end["x"]) / 2
            points = [start, {"x": middle, "y": start["y"]}, {"x": middle, "y": end["y"]}, end]
        elif direction == "LEFT":
            start = {"x": source.x, "y": source.y + source.height / 2}
            end = {"x": target.x + target.width, "y": target.y + target.height / 2}
            middle = (start["x"] + end["x"]) / 2
            points = [start, {"x": middle, "y": start["y"]}, {"x": middle, "y": end["y"]}, end]
        elif direction == "DOWN":
            start = {"x": source.x + source.width / 2, "y": source.y + source.height}
            end = {"x": target.x + target.width / 2, "y": target.y}
            middle = (start["y"] + end["y"]) / 2
            points = [start, {"x": start["x"], "y": middle}, {"x": end["x"], "y": middle}, end]
        else:
            start = {"x": source.x + source.width / 2, "y": source.y}
            end = {"x": target.x + target.width / 2, "y": target.y + target.height}
            middle = (start["y"] + end["y"]) / 2
            points = [start, {"x": start["x"], "y": middle}, {"x": end["x"], "y": middle}, end]
        if not (use_elk_routes and points is elk_points):
            if source_port is not None:
                points[0] = {"x": source_port.x, "y": source_port.y}
            if target_port is not None:
                points[-1] = {"x": target_port.x, "y": target_port.y}
            start, end = points[0], points[-1]
            if horizontal:
                middle = (start["x"] + end["x"]) / 2
                points = [
                    start,
                    {"x": middle, "y": start["y"]},
                    {"x": middle, "y": end["y"]},
                    end,
                ]
            else:
                middle = (start["y"] + end["y"]) / 2
                points = [
                    start,
                    {"x": start["x"], "y": middle},
                    {"x": end["x"], "y": middle},
                    end,
                ]
            points = _route_around_nodes(
                points,
                source_id=edge.source,
                target_id=edge.target,
                bounds_by_id=bounds_by_id,
                horizontal=horizontal,
                forced_vertical_side=route_preferences.get(edge.id),
                ignored_blocker_ids=pass_through_by_edge.get(edge.id),
            )
        scene_edges.append(SceneEdge(
            id=edge.id, source=edge.source, target=edge.target, kind=edge.kind,
            label=edge.label, importance=edge.importance, points=points,
            sourcePointer=f"/model/edges/{index}",
        ))

    scene_groups: list[SceneGroup] = []
    groups_by_id = {group.id: group for group in document.model.groups}
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    for group in document.model.groups:
        if group.parent_id:
            children_by_parent[group.parent_id].append(group.id)
    constrained_members: dict[str, list[str]] = defaultdict(list)
    for constraint in document.intent.constraints:
        if constraint.type == "inside":
            constrained_members[constraint.container].append(
                constraint.element
            )
    group_bounds: dict[str, SceneBounds] = {}

    def resolve_group_bounds(group_id: str) -> SceneBounds | None:
        if group_id in group_bounds:
            return group_bounds[group_id]
        group = groups_by_id[group_id]
        members = [
            bounds_by_id[node_id]
            for node_id in [
                *group.node_ids,
                *constrained_members.get(group_id, []),
            ]
            if node_id in bounds_by_id
        ]
        members.extend(
            child_bounds
            for child_id in sorted(children_by_parent[group_id])
            if (child_bounds := resolve_group_bounds(child_id)) is not None
        )
        if not members:
            return None
        min_x = min(item.x for item in members) - 28
        min_y = min(item.y for item in members) - 48
        max_x = max(item.x + item.width for item in members) + 28
        max_y = max(item.y + item.height for item in members) + 28
        result = SceneBounds(
            x=min_x,
            y=min_y,
            width=max_x - min_x,
            height=max_y - min_y,
        )
        group_bounds[group_id] = result
        return result

    for index, group in enumerate(document.model.groups):
        check_deadline(deadline, operation="compile")
        bounds = resolve_group_bounds(group.id)
        if bounds is None:
            continue
        scene_groups.append(SceneGroup(
            id=group.id, label=group.label, styleRole=group.style_role,
            nodeIds=group.node_ids,
            bounds=bounds,
            sourcePointer=f"/model/groups/{index}",
        ))

    issues: list[DiagramIssue] = []
    scene_edge_by_id = {edge.id: edge for edge in scene_edges}
    scene_group_by_id = {group.id: group for group in scene_groups}
    for index, constraint in enumerate(document.intent.constraints):
        if index % 32 == 0:
            check_deadline(deadline, operation="compile")
        kind = constraint.type
        satisfied = True
        target_element: str | None = None
        if kind == "same-rank":
            positions = [
                (
                    bounds_by_id[element].x
                    if horizontal
                    else bounds_by_id[element].y
                )
                for element in constraint.elements
            ]
            satisfied = max(positions) - min(positions) <= 1
            target_element = constraint.elements[-1]
        elif kind in {"left-of", "right-of", "above", "below"}:
            current = bounds_by_id[constraint.element]
            target = bounds_by_id[constraint.target]
            target_element = constraint.element
            if kind == "left-of":
                satisfied = current.x + current.width <= target.x - 1
            elif kind == "right-of":
                satisfied = current.x >= target.x + target.width + 1
            elif kind == "above":
                satisfied = current.y + current.height <= target.y - 1
            else:
                satisfied = current.y >= target.y + target.height + 1
        elif kind in {"order", "primary-path"}:
            for previous_id, current_id in zip(
                constraint.elements,
                constraint.elements[1:],
            ):
                previous = bounds_by_id[previous_id]
                current = bounds_by_id[current_id]
                if document.intent.direction == "RIGHT":
                    pair_ok = previous.x + previous.width < current.x
                elif document.intent.direction == "LEFT":
                    pair_ok = current.x + current.width < previous.x
                elif document.intent.direction == "DOWN":
                    pair_ok = previous.y + previous.height < current.y
                else:
                    pair_ok = current.y + current.height < previous.y
                satisfied = satisfied and pair_ok
            target_element = constraint.elements[-1]
        elif kind in {"prefer-near", "prefer-apart", "increase-gap"}:
            anchor = bounds_by_id[constraint.elements[0]]
            anchor_center = (
                anchor.x + anchor.width / 2,
                anchor.y + anchor.height / 2,
            )
            distances = []
            for element in constraint.elements[1:]:
                current = bounds_by_id[element]
                distances.append(math.hypot(
                    current.x + current.width / 2 - anchor_center[0],
                    current.y + current.height / 2 - anchor_center[1],
                ))
            if kind == "prefer-near":
                satisfied = all(distance <= major_gap * 1.3 for distance in distances)
            else:
                threshold = major_gap * (1.45 if kind == "increase-gap" else 0.95)
                satisfied = all(distance >= threshold for distance in distances)
            target_element = constraint.elements[-1]
        elif kind == "inside":
            node_bounds = bounds_by_id[constraint.element]
            group = scene_group_by_id.get(constraint.container)
            target_element = constraint.element
            satisfied = bool(
                group
                and group.bounds.x <= node_bounds.x
                and group.bounds.y <= node_bounds.y
                and group.bounds.x + group.bounds.width
                >= node_bounds.x + node_bounds.width
                and group.bounds.y + group.bounds.height
                >= node_bounds.y + node_bounds.height
            )
        elif kind in {"route-above", "route-below"}:
            edge = scene_edge_by_id[constraint.edge]
            obstacle = bounds_by_id[constraint.element]
            routed_y = [
                start["y"]
                for start, end in pairwise(edge.points)
                if start["y"] == end["y"] and start["x"] != end["x"]
            ]
            target_element = constraint.edge
            satisfied = bool(routed_y) and (
                any(value < obstacle.y for value in routed_y)
                if kind == "route-above"
                else any(
                    value > obstacle.y + obstacle.height
                    for value in routed_y
                )
            )
        if not satisfied:
            issues.append(DiagramIssue(
                severity="warning",
                stage="visual",
                code="constraint_unsatisfied",
                json_pointer=f"/intent/constraints/{index}",
                element_id=target_element,
                message=(
                    f"Layout constraint '{kind}' could not be fully satisfied "
                    "without overriding a higher-priority position."
                ),
                suggested_fix=(
                    "Preserve the user-owned pin and revise the conflicting "
                    "constraint or ask the user before moving it."
                ),
                element_ids=[target_element] if target_element else [],
                json_pointers=[f"/intent/constraints/{index}"],
                cause={"type": "conflicting_constraints"},
                suggested_operations=[{"type": "increase-gap"}],
            ))
    for node in scene_nodes:
        if node.label_lines and node.label_lines[-1].endswith("…"):
            issues.append(DiagramIssue(
                severity="warning", stage="visual", code="label_clipped",
                json_pointer=f"{node.source_pointer}/label", element_id=node.id,
                message="The node label exceeds the bounded three-line layout.",
                suggested_fix=(
                    "Shorten the visible label and move detail into description."
                ),
                element_ids=[node.id],
                json_pointers=[f"{node.source_pointer}/label"],
                geometry={"bounds": node.bounds.model_dump()},
                cause={"type": "bounded_label_layout", "max_lines": 3},
            ))
    for index, node in enumerate(scene_nodes):
        if index % 16 == 0:
            check_deadline(deadline, operation="compile")
        left = node.bounds
        for other in scene_nodes[index + 1:]:
            right = other.bounds
            if _bounds_overlap(left, right):
                overlap_bounds = {
                    "x": max(left.x, right.x),
                    "y": max(left.y, right.y),
                    "width": min(left.x + left.width, right.x + right.width) - max(left.x, right.x),
                    "height": min(left.y + left.height, right.y + right.height) - max(left.y, right.y),
                }
                issues.append(DiagramIssue(
                    severity="warning", stage="visual", code="node_overlap",
                    json_pointer=other.source_pointer,
                    element_id=other.id,
                    message=f"Node '{other.id}' overlaps node '{node.id}'.",
                    suggested_fix="Remove the conflicting pin or adjust the relative layout intent.",
                    element_ids=[node.id, other.id],
                    json_pointers=[node.source_pointer, other.source_pointer],
                    geometry={"intersection": overlap_bounds},
                    cause={
                        "type": "fixed_position_conflict",
                        "pinned": [
                            node_id
                            for node_id in (node.id, other.id)
                            if document.view.overrides.get(node_id)
                            and document.view.overrides[node_id].pinned
                        ],
                    },
                    suggested_operations=[{
                        "type": "increase-gap",
                        "elements": [node.id, other.id],
                    }],
                ))
    allowed_pass_through = {
        (item.edge, item.element)
        for item in document.routing_policy.allow_pass_through
    }
    for edge in scene_edges:
        check_deadline(deadline, operation="compile")
        for node in scene_nodes:
            if node.id in {edge.source, edge.target}:
                continue
            if (edge.id, node.id) in allowed_pass_through:
                continue
            if any(
                _segment_intersects_bounds(start, end, node.bounds)
                for start, end in pairwise(edge.points)
            ):
                issues.append(DiagramIssue(
                    severity="warning",
                    stage="visual",
                    code="edge_routes_through_node",
                    json_pointer=edge.source_pointer,
                    element_id=edge.id,
                    message=f"Edge '{edge.id}' routes through node '{node.id}'.",
                    suggested_fix=(
                        "Adjust the layout intent or add spacing around the route."
                    ),
                    element_ids=[edge.id, node.id],
                    json_pointers=[edge.source_pointer, node.source_pointer],
                    geometry={
                        "node_bounds": node.bounds.model_dump(),
                        "segments": [
                            {"start": start, "end": end}
                            for start, end in pairwise(edge.points)
                            if _segment_intersects_bounds(start, end, node.bounds)
                        ],
                    },
                    cause={"type": "route_obstacle", "allowed": False},
                    suggested_operations=[{
                        "type": "route-below",
                        "edge": edge.id,
                        "element": node.id,
                    }],
                ))
                break
    for index, edge in enumerate(scene_edges):
        if index % 8 == 0:
            check_deadline(deadline, operation="compile")
        endpoints = {edge.source, edge.target}
        for other in scene_edges[index + 1:]:
            if endpoints.intersection({other.source, other.target}):
                continue
            intersections = [
                point
                for a_start, a_end in pairwise(edge.points)
                for b_start, b_end in pairwise(other.points)
                if (point := _segment_crossing_point(
                    a_start, a_end, b_start, b_end
                )) is not None
            ]
            if intersections:
                issues.append(DiagramIssue(
                    severity="warning",
                    stage="visual",
                    code="edge_crossing",
                    json_pointer=other.source_pointer,
                    element_id=other.id,
                    message=f"Edge '{other.id}' crosses edge '{edge.id}'.",
                    suggested_fix="Reorder lanes or increase the gap between ranks.",
                    element_ids=[edge.id, other.id],
                    json_pointers=[edge.source_pointer, other.source_pointer],
                    geometry={"intersections": intersections},
                    cause={
                        "type": "route_crossing",
                        "primary_edges": [
                            item.id
                            for item in (edge, other)
                            if item.importance == "primary"
                        ],
                    },
                    suggested_operations=[{
                        "type": "add-bridge",
                        "edge": other.id,
                        "over_edge": edge.id,
                    }],
                ))
                break
    spec = get_diagram_type(document.diagram.family, document.diagram.type)
    quality_policy = spec.quality_policy if spec is not None else {}
    pinned_ids = {
        node_id
        for node_id, override in document.view.overrides.items()
        if override.pinned
    }
    classified_issues: list[DiagramIssue] = []
    crossing_hints: dict[str, list[dict[str, object]]] = defaultdict(list)
    crossing_repairs: list[AutoRepairAction] = []
    for issue_index, issue in enumerate(issues, start=1):
        code = issue.code
        disposition = quality_policy.get(code, "repairable")
        if issue.severity == "error":
            disposition = "blocking"
        if code == "edge_crossing":
            related_edges = [
                scene_edge_by_id[edge_id]
                for edge_id in issue.element_ids
                if edge_id in scene_edge_by_id
            ]
            if any(edge.kind == "note-link" for edge in related_edges):
                disposition = quality_policy.get("note_link_crossing", "render_cue")
            elif any(edge.importance == "primary" for edge in related_edges):
                code = "primary_path_crossing"
                # Incremental edits intentionally keep retained node positions.
                # Moving those nodes merely to remove an otherwise legitimate
                # crossing breaks the user's mental map.  Resolve the ambiguity
                # deterministically instead: keep every primary edge visually
                # continuous and cut a small gap in the non-primary edge.  The
                # rendered cue is an actual compiler repair, not an instruction
                # that the Agent cannot express in semantic source.
                cue_edge = next(
                    (
                        edge
                        for edge in related_edges
                        if edge.importance != "primary"
                    ),
                    related_edges[-1] if related_edges else None,
                )
                if cue_edge is not None:
                    disposition = "render_cue"
                    for intersection in issue.geometry.get("intersections", []):
                        crossing_hints[cue_edge.id].append({
                            **intersection,
                            "style": "gap",
                            "overEdgeId": next(
                                (
                                    edge.id
                                    for edge in related_edges
                                    if edge.importance == "primary"
                                ),
                                "",
                            ),
                        })
                    crossing_repairs.append(AutoRepairAction(
                        issue_id=f"AR-crossing-{issue_index}",
                        code="primary_path_crossing",
                        elements=list(issue.element_ids),
                        action=(
                            "kept the primary path continuous and added a "
                            "crossing gap to the secondary edge"
                        ),
                    ))
            if disposition == "render_cue" and code != "primary_path_crossing":
                for intersection in issue.geometry.get("intersections", []):
                    if issue.element_id:
                        crossing_hints[issue.element_id].append({
                            **intersection,
                            "style": "gap",
                            "overEdgeId": issue.element_ids[0] if issue.element_ids else "",
                        })
        if code == "constraint_unsatisfied" and pinned_ids.intersection(issue.element_ids):
            disposition = "accepted"
        classified_issues.append(issue.model_copy(update={
            "issue_id": f"R{issue_index}",
            "code": code,
            "disposition": disposition,
            "json_pointers": issue.json_pointers or ([issue.json_pointer] if issue.json_pointer else []),
            "element_ids": issue.element_ids or ([issue.element_id] if issue.element_id else []),
            "auto_fixable": disposition == "repairable",
        }))
    issues = classified_issues
    if crossing_hints:
        scene_edges = [
            edge.model_copy(update={"crossings": crossing_hints.get(edge.id, [])})
            for edge in scene_edges
        ]
    all_bounds = [node.bounds for node in scene_nodes] + [group.bounds for group in scene_groups]
    if all_bounds:
        min_x = min(item.x for item in all_bounds) - 48
        min_y = min(item.y for item in all_bounds) - 48
        max_x = max(item.x + item.width for item in all_bounds) + 48
        max_y = max(item.y + item.height for item in all_bounds) + 48
    else:
        min_x = min_y = 0
        max_x, max_y = 800, 500
    canvas_width = max_x - min_x
    canvas_height = max_y - min_y
    check_canvas_extent(canvas_width, canvas_height)
    resolved_repairs = [
        AutoRepairAction(
            issue_id=f"AR{index}",
            code="node_overlap",
            elements=list(pair),
            action="shifted the movable node on the minor axis",
        )
        for index, pair in enumerate(
            sorted(collisions_before_repair - collisions_after_repair),
            start=1,
        )
    ] + crossing_repairs
    scene = DiagramScene(
        diagramId=document.id, title=document.title, family=document.diagram.family,
        diagramType=document.diagram.type, compilerVersion=COMPILER_VERSION,
        themeVersion=THEME_VERSION,
        bounds=SceneBounds(
            x=min_x,
            y=min_y,
            width=canvas_width,
            height=canvas_height,
        ),
        nodes=scene_nodes, edges=scene_edges, groups=scene_groups, issues=issues,
        auto_repair=AutoRepairReport(
            attempted=bool(collisions_before_repair or crossing_repairs),
            passes=2 if collisions_before_repair else (1 if crossing_repairs else 0),
            resolved=resolved_repairs,
            remaining_issue_ids=[
                issue.issue_id
                for issue in issues
                if issue.disposition in {"blocking", "repairable"}
            ],
        ),
    )
    check_deadline(deadline, operation="compile")
    scene_size = len(json.dumps(
        scene.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode())
    if scene_size > MAX_SCENE_BYTES:
        raise DiagramLimitError(
            "scene_too_large",
            f"Compiled Scene IR exceeds the {MAX_SCENE_BYTES}-byte limit.",
        )
    return scene


def _segment_intersects_bounds(
    start: dict[str, float],
    end: dict[str, float],
    bounds: SceneBounds,
) -> bool:
    epsilon = 0.01
    if abs(start["y"] - end["y"]) < epsilon:
        y = start["y"]
        low, high = sorted((start["x"], end["x"]))
        return (
            bounds.y + epsilon < y < bounds.y + bounds.height - epsilon
            and low < bounds.x + bounds.width - epsilon
            and high > bounds.x + epsilon
        )
    if abs(start["x"] - end["x"]) < epsilon:
        x = start["x"]
        low, high = sorted((start["y"], end["y"]))
        return (
            bounds.x + epsilon < x < bounds.x + bounds.width - epsilon
            and low < bounds.y + bounds.height - epsilon
            and high > bounds.y + epsilon
        )
    return False


def _segments_cross(
    first_start: dict[str, float],
    first_end: dict[str, float],
    second_start: dict[str, float],
    second_end: dict[str, float],
) -> bool:
    epsilon = 0.01
    first_horizontal = abs(first_start["y"] - first_end["y"]) < epsilon
    second_horizontal = abs(second_start["y"] - second_end["y"]) < epsilon
    if first_horizontal == second_horizontal:
        return False
    horizontal_start, horizontal_end = (
        (first_start, first_end)
        if first_horizontal
        else (second_start, second_end)
    )
    vertical_start, vertical_end = (
        (second_start, second_end)
        if first_horizontal
        else (first_start, first_end)
    )
    horizontal_low, horizontal_high = sorted(
        (horizontal_start["x"], horizontal_end["x"])
    )
    vertical_low, vertical_high = sorted(
        (vertical_start["y"], vertical_end["y"])
    )
    return (
        horizontal_low + epsilon < vertical_start["x"] < horizontal_high - epsilon
        and vertical_low + epsilon < horizontal_start["y"] < vertical_high - epsilon
    )


def _segment_crossing_point(
    first_start: dict[str, float],
    first_end: dict[str, float],
    second_start: dict[str, float],
    second_end: dict[str, float],
) -> dict[str, float] | None:
    if not _segments_cross(first_start, first_end, second_start, second_end):
        return None
    first_horizontal = abs(first_start["y"] - first_end["y"]) < 0.01
    horizontal = (first_start, first_end) if first_horizontal else (second_start, second_end)
    vertical = (second_start, second_end) if first_horizontal else (first_start, first_end)
    return {
        "x": round(vertical[0]["x"], 2),
        "y": round(horizontal[0]["y"], 2),
    }
