"""Side-effect-free validation for one immutable Execution Plan source file."""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .schema import ExecutionPlanV1

MAX_PLAN_BYTES = 512 * 1024
MAX_JSON_DEPTH = 64
MAX_ISSUES = 30
_PLAN_PATH = re.compile(r"^/data/plans/[A-Za-z0-9][A-Za-z0-9_.-]*\.plan\.json$")
_NODE_ID = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class PlanValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    node_id: str | None = None
    json_pointer: str = ""
    message: str
    suggested_fix: str
    line: int | None = None
    column: int | None = None


class PlanValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    plan_path: str
    source_hash: str | None = None
    source_size_bytes: int = Field(default=0, ge=0)
    errors: list[PlanValidationIssue] = Field(default_factory=list)
    truncated: bool = False
    definition: ExecutionPlanV1 | None = None


class _DuplicateKey(ValueError):
    pass


def _pointer(parts: tuple[Any, ...] | list[Any]) -> str:
    escaped = [str(item).replace("~", "~0").replace("/", "~1") for item in parts]
    return "/" + "/".join(escaped) if escaped else ""


def _issue(
    code: str,
    message: str,
    suggested_fix: str,
    *,
    pointer: str = "",
    node_id: str | None = None,
    line: int | None = None,
    column: int | None = None,
) -> PlanValidationIssue:
    return PlanValidationIssue(
        code=code,
        node_id=node_id,
        json_pointer=pointer,
        message=message,
        suggested_fix=suggested_fix,
        line=line,
        column=column,
    )


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _depth(value: Any, level: int = 1) -> int:
    if isinstance(value, dict):
        return max([level, *(_depth(item, level + 1) for item in value.values())])
    if isinstance(value, list):
        return max([level, *(_depth(item, level + 1) for item in value)])
    return level


def _edges(plan: ExecutionPlanV1) -> dict[str, list[str]]:
    return {
        node.id: list(node.next) if hasattr(node, "next") else []
        for node in plan.nodes
    }


def _reachable(edges: dict[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(edges.get(current, []))
    return seen


def _reachable_before(
    edges: dict[str, list[str]], start: str, stop: str,
) -> set[str]:
    """Nodes reachable from start without crossing the matching merge."""
    seen: set[str] = set()
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == stop or current in seen:
            continue
        seen.add(current)
        queue.extend(edges.get(current, []))
    return seen


def _graph_issues(plan: ExecutionPlanV1) -> list[PlanValidationIssue]:
    issues: list[PlanValidationIssue] = []
    nodes = {node.id: node for node in plan.nodes}
    starts = [node.id for node in plan.nodes if node.type == "start"]
    ends = [node.id for node in plan.nodes if node.type == "end"]
    if len(starts) != 1:
        issues.append(_issue(
            "start_count",
            f"plan requires exactly one start node; found {len(starts)}",
            "Keep one node with type=start",
            pointer="/nodes",
        ))
    if len(ends) != 1:
        issues.append(_issue(
            "end_count",
            f"plan requires exactly one end node; found {len(ends)}",
            "Keep one node with type=end",
            pointer="/nodes",
        ))

    seen_ids: set[str] = set()
    for index, node in enumerate(plan.nodes):
        if not _NODE_ID.fullmatch(node.id):
            issues.append(_issue(
                "invalid_node_id",
                f"invalid node id: {node.id!r}",
                "Use short snake_case ids beginning with a lowercase letter",
                pointer=f"/nodes/{index}/id",
                node_id=node.id,
            ))
        if node.id in seen_ids:
            issues.append(_issue(
                "duplicate_node_id",
                f"duplicate node id: {node.id}",
                "Give every top-level node a unique id",
                pointer=f"/nodes/{index}/id",
                node_id=node.id,
            ))
        seen_ids.add(node.id)
        targets = list(getattr(node, "next", []))
        if len(targets) != len(set(targets)):
            issues.append(_issue(
                "duplicate_control_target",
                f"node {node.id} lists the same next target more than once",
                "Keep every target once in the next array",
                pointer=f"/nodes/{index}/next",
                node_id=node.id,
            ))

    edges = _edges(plan)
    for source, targets in edges.items():
        for target in targets:
            if target not in nodes:
                issues.append(_issue(
                    "unknown_control_target",
                    f"{source} points to unknown node {target}",
                    "Use the id of an existing top-level node",
                    node_id=source,
                ))
    if len(starts) == 1 and any(starts[0] in targets for targets in edges.values()):
        issues.append(_issue(
            "start_has_predecessor",
            "the start node cannot have an incoming edge",
            "Remove every edge targeting the start node",
            node_id=starts[0],
        ))
    if any(issue.code in {
        "duplicate_node_id", "duplicate_control_target", "unknown_control_target",
    } for issue in issues):
        return issues

    indegree = {node_id: 0 for node_id in nodes}
    predecessors = {node_id: [] for node_id in nodes}
    for source, targets in edges.items():
        for target in targets:
            indegree[target] += 1
            predecessors[target].append(source)
    queue = deque(sorted(node_id for node_id, degree in indegree.items() if degree == 0))
    visited: list[str] = []
    while queue:
        current = queue.popleft()
        visited.append(current)
        for target in edges[current]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if len(visited) != len(nodes):
        issues.append(_issue(
            "control_cycle",
            "control flow must be a directed acyclic graph",
            "Remove backward edges or loop nodes; loops are not supported in V1",
            pointer="/nodes",
        ))
        return issues

    if len(starts) == 1:
        reachable = _reachable(edges, starts[0])
        for node_id in sorted(set(nodes) - reachable):
            issues.append(_issue(
                "node_unreachable_from_start",
                f"node {node_id} is not reachable from start",
                "Connect it to the control flow or remove it",
                node_id=node_id,
            ))
    if len(ends) == 1:
        reverse = {node_id: [] for node_id in nodes}
        for source, targets in edges.items():
            for target in targets:
                reverse[target].append(source)
        reaches_end = _reachable(reverse, ends[0])
        for node_id in sorted(set(nodes) - reaches_end):
            issues.append(_issue(
                "node_cannot_reach_end",
                f"node {node_id} cannot reach end",
                "Connect every terminal path to the single end node",
                node_id=node_id,
            ))

    # Restrict the otherwise-general DAG to structured fork/join regions.  The
    # immediate post-dominator is the first node every path from a split must
    # pass through; it is therefore the split's deterministic merge point.
    if len(ends) == 1 and len(visited) == len(nodes):
        end_id = ends[0]
        postdominators = {node_id: set(nodes) for node_id in nodes}
        postdominators[end_id] = {end_id}
        for node_id in reversed(visited):
            if node_id == end_id:
                continue
            successors = edges[node_id]
            if successors:
                postdominators[node_id] = {
                    node_id,
                    *set.intersection(*(postdominators[item] for item in successors)),
                }
            else:
                postdominators[node_id] = {node_id}

        split_scopes: list[tuple[str, str, set[str]]] = []
        for split_id, successors in edges.items():
            if len(successors) < 2:
                continue
            strict = postdominators[split_id] - {split_id}
            if not strict:
                issues.append(_issue(
                    "split_without_merge",
                    f"split node {split_id} has no common merge",
                    "Connect every branch to one shared downstream node",
                    node_id=split_id,
                ))
                continue
            merge_id = max(strict, key=lambda item: len(postdominators[item]))
            if len(predecessors.get(merge_id) or []) < 2:
                issues.append(_issue(
                    "split_merge_not_join",
                    f"the common successor {merge_id} is not a merge node",
                    "Route the split branches into a node with multiple incoming edges",
                    node_id=split_id,
                ))
                continue
            branch_regions = [
                _reachable_before(edges, successor, merge_id)
                for successor in successors
            ]
            overlap = set()
            for left_index, left in enumerate(branch_regions):
                for right in branch_regions[left_index + 1:]:
                    overlap.update(left & right)
            if overlap:
                first = sorted(overlap)[0]
                issues.append(_issue(
                    "split_branches_overlap_before_merge",
                    f"split branches overlap at {first} before merge {merge_id}",
                    "Keep branches disjoint until their single shared merge",
                    node_id=split_id,
                ))
            region = set().union(*branch_regions) if branch_regions else set()
            split_scopes.append((split_id, merge_id, region))

        # Structured parallel scopes must be disjoint or properly nested. This
        # rejects crossing fork/join pairs whose completion semantics would be
        # ambiguous even though the raw graph is acyclic.
        for index, (left_split, left_merge, left_region) in enumerate(split_scopes):
            left_scope = left_region | {left_split, left_merge}
            for right_split, right_merge, right_region in split_scopes[index + 1:]:
                right_scope = right_region | {right_split, right_merge}
                if not left_scope & right_scope:
                    continue
                left_contains = right_split in left_region and (
                    right_merge in left_region or right_merge == left_merge
                )
                right_contains = left_split in right_region and (
                    left_merge in right_region or left_merge == right_merge
                )
                if not left_contains and not right_contains:
                    issues.append(_issue(
                        "crossing_split_merge_scopes",
                        f"parallel scopes {left_split}->{left_merge} and "
                        f"{right_split}->{right_merge} cross",
                        "Make parallel regions disjoint or fully nested",
                        node_id=left_split,
                    ))

    return issues


def validate_plan_bytes(plan_path: str, raw: bytes) -> PlanValidationReport:
    """Parse and fully validate one source snapshot without durable side effects."""
    source_hash = "sha256:" + hashlib.sha256(raw).hexdigest()
    base = {
        "status": "invalid",
        "plan_path": plan_path,
        "source_hash": source_hash,
        "source_size_bytes": len(raw),
    }
    if not _PLAN_PATH.fullmatch(plan_path) or ".." in plan_path.split("/"):
        return PlanValidationReport(**base, errors=[_issue(
            "invalid_plan_path",
            "plan_path must be a .plan.json file directly under /data/plans",
            "Write the plan to /data/plans/<descriptive-name>.plan.json",
        )])
    if len(raw) > MAX_PLAN_BYTES:
        return PlanValidationReport(**base, errors=[_issue(
            "plan_too_large",
            f"plan source exceeds {MAX_PLAN_BYTES} bytes",
            "Reduce the plan to at most 30 concise top-level nodes",
        )])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return PlanValidationReport(**base, errors=[_issue(
            "invalid_utf8",
            "plan source is not valid UTF-8",
            "Save the JSON file as UTF-8",
        )])
    try:
        value = json.loads(text, object_pairs_hook=_object_no_duplicates)
    except _DuplicateKey as exc:
        return PlanValidationReport(**base, errors=[_issue(
            "duplicate_json_key",
            f"duplicate JSON key: {str(exc)!r}",
            "Keep each object key exactly once",
        )])
    except json.JSONDecodeError as exc:
        return PlanValidationReport(**base, errors=[_issue(
            "invalid_json",
            "plan source is not valid JSON",
            "Fix the JSON syntax at the reported line and column",
            line=exc.lineno,
            column=exc.colno,
        )])
    if _depth(value) > MAX_JSON_DEPTH:
        return PlanValidationReport(**base, errors=[_issue(
            "json_depth_exceeded",
            f"plan JSON exceeds maximum depth {MAX_JSON_DEPTH}",
            "Flatten deeply nested task or graph data",
        )])
    try:
        definition = ExecutionPlanV1.model_validate(value)
    except ValidationError as exc:
        errors = [
            _issue(
                "schema_validation_failed",
                str(error.get("msg") or "invalid plan field")[:240],
                "Match the ExecutionPlanV1 field contract for this node type",
                pointer=_pointer(error.get("loc") or ()),
            )
            for error in exc.errors(include_url=False, include_context=False)
        ]
        truncated = len(errors) > MAX_ISSUES
        return PlanValidationReport(
            **base,
            errors=errors[:MAX_ISSUES],
            truncated=truncated,
        )
    errors = _graph_issues(definition)
    errors.sort(key=lambda item: (item.json_pointer, item.node_id or "", item.code))
    truncated = len(errors) > MAX_ISSUES
    return PlanValidationReport(
        status="invalid" if errors else "valid",
        plan_path=plan_path,
        source_hash=source_hash,
        source_size_bytes=len(raw),
        errors=errors[:MAX_ISSUES],
        truncated=truncated,
        definition=definition if not errors else None,
    )
