from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, ValidationError
from PIL import Image

from vibecanvas_api.diagrams.agent_contract import (
    DIAGRAM_TOOL_CONTRACTS,
    diagram_agent_prompt,
)
from vibecanvas_api.diagrams.compiler import compile_diagram
from vibecanvas_api.diagrams.limits import (
    MAX_CANVAS_EXTENT,
    MAX_PNG_BYTES,
    MAX_REVIEW_PIXELS,
    DiagramLimitError,
)
from vibecanvas_api.diagrams.mcp_contract import (
    DIAGRAM_INPUT_SCHEMAS,
    DIAGRAM_OUTPUT_SCHEMAS,
)
from vibecanvas_api.diagrams.models import DiagramDocument
from vibecanvas_api.diagrams.registry import (
    ALLOWED_CONSTRAINTS,
    get_diagram_type,
    list_enabled_types,
)
from vibecanvas_api.diagrams.render import (
    _FONT_PATH,
    render_scene_png,
    render_scene_svg,
)
from vibecanvas_api.diagrams.validator import (
    MAX_JSON_DEPTH,
    MAX_SOURCE_BYTES,
    parse_and_validate,
)
from vibecanvas_api.diagrams.visual_tokens import (
    DIAGRAM_THEME_PALETTES,
    THEME_VERSION,
)


def _document(*, missing_target: bool = False) -> dict:
    spec = get_diagram_type("flow", "basic")
    assert spec is not None
    return {
        "schemaVersion": 1,
        "id": "request-flow",
        "title": "Request flow",
        "diagram": {"family": "flow", "type": "basic"},
        "model": {
            "nodes": [
                {"id": "start", "kind": "start", "label": "Start", "styleRole": "primary"},
                {"id": "process", "kind": "process", "label": "Process", "styleRole": "neutral"},
                {"id": "done", "kind": "end", "label": "Done", "styleRole": "success"},
            ],
            "edges": [
                {"id": "start-process", "source": "start", "target": "missing" if missing_target else "process", "kind": "flow"},
                {"id": "process-done", "source": "process", "target": "done", "kind": "flow"},
            ],
            "groups": [],
            "embeds": [],
            "resources": [],
        },
        "intent": {"direction": "RIGHT", "density": "comfortable", "stability": "preserve", "primaryPath": ["start", "process", "done"], "constraints": []},
        "view": {"layoutMode": "auto", "overrides": {}, "frames": []},
        "metadata": {"createdBy": "agent", "specVersion": "2026.08.1", "specHash": spec.spec_hash, "compilerVersion": None, "themeVersion": None},
    }


def _golden_document(family: str, diagram_type: str) -> dict:
    architecture = family == "architecture"
    ids = ("user", "service", "data") if architecture else (
        "start", "process", "done"
    )
    kinds = ("actor", "service", "database") if architecture else (
        "start", "process", "end"
    )
    spec = get_diagram_type(family, diagram_type)
    assert spec is not None
    return {
        "schemaVersion": 1,
        "id": f"{family}-golden",
        "title": f"{family.title()} Golden",
        "diagram": {"family": family, "type": diagram_type},
        "model": {
            "nodes": [
                {
                    "id": ids[0], "kind": kinds[0], "label": "Customer 用户",
                    "styleRole": "actor" if architecture else "primary",
                },
                {
                    "id": ids[1], "kind": kinds[1],
                    "label": "Application Service",
                    "description": "Validates and processes requests",
                    "styleRole": "service" if architecture else "neutral",
                },
                {
                    "id": ids[2], "kind": kinds[2], "label": "Durable Store",
                    "styleRole": "storage" if architecture else "success",
                },
            ],
            "edges": [
                {
                    "id": "e1", "source": ids[0], "target": ids[1],
                    "kind": "request" if architecture else "flow",
                    "label": "Request",
                },
                {
                    "id": "e2", "source": ids[1], "target": ids[2],
                    "kind": "data-flow" if architecture else "flow",
                    "label": "Persist",
                },
            ],
            "groups": ([{
                "id": "boundary", "label": "Trusted boundary",
                "nodeIds": [ids[1], ids[2]],
            }] if architecture else []),
            "embeds": [],
            "resources": [],
        },
        "intent": {
            "direction": "RIGHT", "density": "comfortable",
            "stability": "preserve", "primaryPath": list(ids),
            "constraints": [{"type": "primary-path", "elements": list(ids)}],
        },
        "view": {"layoutMode": "auto", "overrides": {}, "frames": []},
        "metadata": {
            "createdBy": "agent", "specVersion": "2026.08.1",
            "specHash": spec.spec_hash, "compilerVersion": None,
            "themeVersion": None,
        },
    }


def _crossing_document(*, primary: bool = False) -> dict:
    value = _golden_document("architecture", "system-container")
    positions = {
        "a": (0, 0),
        "b": (632, 300),
        "c": (0, 123),
        "d": (800, 500),
    }
    value["model"]["nodes"] = [
        {"id": node_id, "kind": "service", "label": node_id.upper()}
        for node_id in positions
    ]
    value["model"]["edges"] = [
        {
            "id": "ab",
            "source": "a",
            "target": "b",
            "kind": "flow",
            "importance": "primary" if primary else "secondary",
        },
        {"id": "cd", "source": "c", "target": "d", "kind": "flow"},
    ]
    value["model"]["groups"] = []
    value["intent"]["primaryPath"] = []
    value["intent"]["constraints"] = []
    value["view"]["layoutMode"] = "preserve"
    value["view"]["overrides"] = {
        node_id: {
            "position": {"x": position[0], "y": position[1]},
            "owner": "agent",
        }
        for node_id, position in positions.items()
    }
    return value


def _scene_golden_signature(scene) -> dict:
    return {
        "bounds": scene.bounds.model_dump(),
        "nodes": [{
            "id": node.id,
            "bounds": node.bounds.model_dump(),
            "labelLines": node.label_lines,
            "descriptionLines": node.description_lines,
        } for node in scene.nodes],
        "edges": [{"id": edge.id, "points": edge.points} for edge in scene.edges],
        "groups": [{
            "id": group.id,
            "bounds": group.bounds.model_dump(),
        } for group in scene.groups],
    }


def test_registry_only_advertises_enabled_compiler_types() -> None:
    keys = [item.key for item in list_enabled_types()]
    assert keys == ["architecture/system-container", "flow/basic"]
    prompt = diagram_agent_prompt()
    assert all(key in prompt for key in keys)
    assert all(name in prompt for name in DIAGRAM_TOOL_CONTRACTS)
    assert "CREATE workflow" in prompt
    assert "MODIFY workflow" in prompt
    assert "uml/sequence" not in prompt
    assert (
        "schemaVersion, id, title, diagram, model, intent, view, "
        "routingPolicy, metadata"
    ) in prompt
    assert "no more than 3 visual review rounds" in prompt
    assert "Treat every tool `next.action` as mandatory control flow" in prompt
    assert "after review_diagram returns `edit_source`, do not answer the user" in prompt
    assert "A `deliver` review proves structural readiness" in prompt
    normalized_prompt = " ".join(prompt.split())
    assert "split, replace or merge an element consumes" in normalized_prompt
    assert "generic completion statement does not satisfy" in normalized_prompt
    assert "modification is an auto-saved revision of the same canonical path" in normalized_prompt
    assert "intentional copy is a separate create operation" in normalized_prompt
    assert len(prompt.split()) < 1200


def test_valid_document_compiles_to_deterministic_scene_and_source_map() -> None:
    raw = json.dumps(_document(), sort_keys=True)
    document, issues = parse_and_validate(raw)
    assert issues == []
    assert isinstance(document, DiagramDocument)
    first = compile_diagram(document)
    second = compile_diagram(document)
    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(mode="json", by_alias=True)
    assert [node.id for node in first.nodes] == ["start", "process", "done"]
    assert first.nodes[1].source_pointer == "/model/nodes/1"
    assert first.edges[0].points[0]["x"] < first.edges[0].points[-1]["x"]


def test_quality_policy_is_complete_for_every_enabled_diagram_type() -> None:
    required_codes = {
        "node_overlap",
        "edge_routes_through_node",
        "primary_path_crossing",
        "edge_crossing",
        "note_link_crossing",
        "label_clipped",
        "canvas_clipped",
        "constraint_unsatisfied",
    }
    allowed_dispositions = {"blocking", "repairable", "render_cue", "accepted"}

    for spec in list_enabled_types():
        assert required_codes <= set(spec.quality_policy), spec.key
        assert set(spec.quality_policy.values()) <= allowed_dispositions, spec.key


def test_auto_repair_is_bounded_and_deterministic() -> None:
    value = _document()
    value["intent"]["constraints"] = [{
        "type": "same-rank",
        "elements": ["start", "process", "done"],
    }]
    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    first = compile_diagram(document)
    second = compile_diagram(document)
    assert first.model_dump(mode="json", by_alias=True) == second.model_dump(
        mode="json", by_alias=True
    )
    assert first.auto_repair.attempted is True
    assert first.auto_repair.passes == 2
    assert first.auto_repair.resolved
    assert not any(
        issue.code == "node_overlap"
        and issue.disposition in {"blocking", "repairable"}
        for issue in first.issues
    )


def test_crossing_policy_distinguishes_render_cues_from_primary_path_defects() -> None:
    ordinary, issues = parse_and_validate(json.dumps(_crossing_document()))
    assert ordinary is not None and issues == []
    ordinary_scene = compile_diagram(ordinary)
    crossing = next(
        issue for issue in ordinary_scene.issues if issue.code == "edge_crossing"
    )
    assert crossing.disposition == "render_cue"
    assert crossing.auto_fixable is False
    bridged = next(edge for edge in ordinary_scene.edges if edge.id == "cd")
    assert bridged.crossings
    assert all(item["style"] == "gap" for item in bridged.crossings)

    primary, issues = parse_and_validate(json.dumps(_crossing_document(primary=True)))
    assert primary is not None and issues == []
    primary_scene = compile_diagram(primary)
    crossing = next(
        issue
        for issue in primary_scene.issues
        if issue.code == "primary_path_crossing"
    )
    assert crossing.disposition == "render_cue"
    assert crossing.auto_fixable is False
    primary_edges = {
        edge.id for edge in primary_scene.edges if edge.importance == "primary"
    }
    assert all(not edge.crossings for edge in primary_scene.edges if edge.id in primary_edges)
    secondary_cues = [
        cue
        for edge in primary_scene.edges
        if edge.id not in primary_edges
        for cue in edge.crossings
    ]
    assert secondary_cues
    assert all(cue["style"] == "gap" for cue in secondary_cues)
    assert any(
        repair.code == "primary_path_crossing"
        for repair in primary_scene.auto_repair.resolved
    )


def test_elk_layered_layout_breaks_feedback_cycles_without_route_warnings() -> None:
    value = _document()
    value["model"]["edges"].append({
        "id": "feedback",
        "source": "done",
        "target": "start",
        "kind": "flow",
        "label": "retry",
    })
    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    by_id = {node.id: node.bounds for node in scene.nodes}
    assert by_id["start"].x < by_id["process"].x < by_id["done"].x
    assert not {
        "node_overlap",
        "edge_routes_through_node",
        "edge_crossing",
    }.intersection(issue.code for issue in scene.issues)


def test_every_preview_registry_type_matches_scene_golden() -> None:
    golden_path = Path(__file__).parent / "goldens" / "graph-scenes.json"
    expected = json.loads(golden_path.read_text())
    enabled = {item.key for item in list_enabled_types()}

    assert set(expected) == enabled
    for key in sorted(enabled):
        family, diagram_type = key.split("/", 1)
        document, issues = parse_and_validate(json.dumps(
            _golden_document(family, diagram_type),
            ensure_ascii=False,
        ))
        assert document is not None and issues == []
        assert _scene_golden_signature(compile_diagram(document)) == expected[key]


def test_compiler_emits_stable_multilingual_label_wrapping() -> None:
    value = _document()
    value["model"]["nodes"][1]["label"] = (
        "处理用户请求 Process customer request 🚀 并写入数据库"
    )
    value["model"]["nodes"][1]["description"] = (
        "中英文 mixed description with emoji ✅ remains bounded and readable"
    )
    document, issues = parse_and_validate(json.dumps(value, ensure_ascii=False))

    assert document is not None and issues == []
    first = compile_diagram(document).nodes[1]
    second = compile_diagram(document).nodes[1]
    assert first.label_lines == second.label_lines
    assert 1 < len(first.label_lines) <= 3
    assert len(first.description_lines) <= 3


def test_renderers_honor_transparent_and_theme_backgrounds() -> None:
    document, issues = parse_and_validate(json.dumps(_document()))
    assert document is not None and issues == []
    scene = compile_diagram(document)

    transparent_png = Image.open(io.BytesIO(render_scene_png(
        scene,
        background="transparent",
    )))
    assert transparent_png.mode == "RGBA"
    assert transparent_png.getpixel((0, 0))[3] == 0
    transparent_svg = render_scene_svg(
        scene,
        background="transparent",
    ).decode()
    themed_svg = render_scene_svg(scene, background="theme").decode()
    assert 'fill="#fafbfd"' not in transparent_svg
    assert 'fill="#fafbfd"' in themed_svg


def test_export_renderers_keep_labels_inside_a_visual_safety_margin() -> None:
    document, issues = parse_and_validate(json.dumps(_document()))
    assert document is not None and issues == []
    scene = compile_diagram(document)
    edge = scene.edges[0]
    edge_at_top = edge.model_copy(update={
        "label": "Top edge label",
        "points": [
            {"x": point["x"], "y": scene.bounds.y}
            for point in edge.points
        ],
    })
    scene = scene.model_copy(update={
        "edges": [edge_at_top, *scene.edges[1:]],
    })

    svg = render_scene_svg(scene, background="white").decode()
    assert (
        f'viewBox="{scene.bounds.x - 24.0} {scene.bounds.y - 24.0} '
        in svg
    )
    png = Image.open(io.BytesIO(render_scene_png(
        scene,
        background="white",
    ))).convert("RGB")
    assert all(png.getpixel((x, 0)) == (255, 255, 255) for x in range(png.width))


def test_preview_and_export_theme_tokens_are_identical() -> None:
    browser_tokens_path = (
        Path(__file__).resolve().parents[3]
        / "web/src/lib/preview/diagram-visual-tokens.json"
    )
    browser_tokens = json.loads(browser_tokens_path.read_text(encoding="utf-8"))
    assert browser_tokens.pop("version") == THEME_VERSION
    assert browser_tokens == DIAGRAM_THEME_PALETTES

    document, issues = parse_and_validate(json.dumps(_document()))
    assert document is not None and issues == []
    scene = compile_diagram(document)
    for theme, palette in DIAGRAM_THEME_PALETTES.items():
        svg = render_scene_svg(scene, theme=theme, background="theme").decode()
        assert f'fill="{palette["background"]}"' in svg
        assert f'stroke="{palette["edge"]}"' in svg
        assert f'fill="{palette["roleFills"]["primary"]}"' in svg


def test_review_renderer_uses_the_packaged_cjk_font() -> None:
    packaged_font = Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
    if not packaged_font.is_file():
        pytest.skip("fonts-wqy-zenhei is an external native/Docker dependency")
    assert _FONT_PATH.endswith("/wqy/wqy-zenhei.ttc")
    assert Path(_FONT_PATH) == packaged_font


def test_semantic_reference_error_is_source_addressable() -> None:
    document, issues = parse_and_validate(json.dumps(_document(missing_target=True)))
    assert document is not None
    target = next(issue for issue in issues if issue.code == "edge_target_not_found")
    assert target.json_pointer == "/model/edges/0/target"
    assert target.element_id == "start-process"


def test_unregistered_type_is_rejected_instead_of_emulated() -> None:
    value = _document()
    value["diagram"] = {"family": "uml", "type": "sequence"}
    document, issues = parse_and_validate(json.dumps(value))
    assert document is not None
    assert any(issue.code == "diagram_type_not_enabled" for issue in issues)


def test_external_url_include_and_script_metadata_are_fail_closed() -> None:
    for key in ("url", "include", "script"):
        value = _document()
        value["model"]["nodes"][0]["metadata"] = {
            key: "https://outside.example/resource"
        }
        document, issues = parse_and_validate(json.dumps(value))
        assert document is not None
        issue = next(
            item for item in issues
            if item.code == "external_resource_forbidden"
        )
        assert issue.json_pointer == f"/model/nodes/0/metadata/{key}"


def test_source_size_and_nesting_limits_are_enforced() -> None:
    document, issues = parse_and_validate(b" " * (MAX_SOURCE_BYTES + 1))
    assert document is None
    assert issues[0].code == "source_too_large"

    nested: object = "leaf"
    for _ in range(MAX_JSON_DEPTH + 2):
        nested = {"nested": nested}
    document, issues = parse_and_validate(json.dumps(nested))
    assert document is None
    assert issues[0].code == "source_too_deep"


def test_dangling_port_and_unregistered_asset_are_rejected() -> None:
    value = _document()
    value["model"]["nodes"][1]["ports"] = [
        {"id": "input", "direction": "in"},
    ]
    value["model"]["nodes"][1]["assetRef"] = "platform.not-real"
    value["model"]["edges"][0]["targetPort"] = "missing"

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None
    assert {issue.code for issue in issues} >= {
        "asset_not_found",
        "edge_target_port_not_found",
    }


def test_declared_ports_are_compiled_and_used_for_edge_routes() -> None:
    value = _document()
    value["model"]["nodes"][0]["ports"] = [
        {"id": "out", "direction": "out", "side": "SOUTH"},
    ]
    value["model"]["nodes"][1]["ports"] = [
        {"id": "in", "direction": "in", "side": "NORTH"},
    ]
    value["model"]["edges"][0]["sourcePort"] = "out"
    value["model"]["edges"][0]["targetPort"] = "in"

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    start = scene.nodes[0].ports[0]
    process = scene.nodes[1].ports[0]
    edge = scene.edges[0]
    assert edge.points[0] == {"x": start.x, "y": start.y}
    assert edge.points[-1] == {"x": process.x, "y": process.y}
    assert start.source_pointer == "/model/nodes/0/ports/0"


def test_group_cycle_is_rejected_with_source_pointer() -> None:
    value = _document()
    value["model"]["groups"] = [
        {"id": "outer", "label": "Outer", "parentId": "inner"},
        {"id": "inner", "label": "Inner", "parentId": "outer"},
    ]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None
    cycle = next(issue for issue in issues if issue.code == "group_cycle")
    assert cycle.json_pointer.startswith("/model/groups/")
    assert cycle.element_id in {"outer", "inner"}


def test_nested_group_bounds_include_child_padding() -> None:
    value = _document()
    value["model"]["groups"] = [
        {"id": "outer", "label": "Outer", "nodeIds": ["start"]},
        {
            "id": "inner",
            "label": "Inner",
            "nodeIds": ["process"],
            "parentId": "outer",
        },
    ]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    groups = {group.id: group for group in scene.groups}
    outer = groups["outer"].bounds
    inner = groups["inner"].bounds
    assert outer.x < inner.x
    assert outer.y < inner.y
    assert outer.x + outer.width > inner.x + inner.width
    assert outer.y + outer.height > inner.y + inner.height


def test_layout_modes_preserve_only_the_positions_they_own() -> None:
    auto_value = _document()
    auto_value["view"]["overrides"] = {
        "process": {
            "position": {"x": 900, "y": 700},
            "owner": "agent",
        },
        "done": {
            "position": {"x": 1000, "y": 800},
            "owner": "user",
            "pinned": True,
        },
    }
    auto_document, issues = parse_and_validate(json.dumps(auto_value))
    assert auto_document is not None and issues == []
    auto_scene = compile_diagram(auto_document)
    auto_by_id = {node.id: node for node in auto_scene.nodes}
    assert auto_by_id["process"].bounds.x != 900
    assert auto_by_id["done"].bounds.x == 1000

    incremental_value = _document()
    incremental_value["view"]["layoutMode"] = "incremental"
    incremental_value["intent"]["constraints"] = [
        {"type": "left-of", "element": "process", "target": "start"},
    ]
    incremental_value["view"]["overrides"] = {
        "start": {
            "position": {"x": 120, "y": 220},
            "owner": "compiler",
        },
        "process": {
            "position": {"x": 900, "y": 700},
            "nudge": {"dx": 2, "dy": -1, "unit": "grid"},
            "owner": "agent",
        },
    }
    incremental_document, issues = parse_and_validate(
        json.dumps(incremental_value)
    )
    assert incremental_document is not None and issues == []
    incremental_scene = compile_diagram(incremental_document)
    incremental_by_id = {node.id: node for node in incremental_scene.nodes}
    # inspect-provided compiler positions are retained exactly; ELK places
    # only nodes without an incremental override.
    assert incremental_by_id["start"].bounds.x == 120
    assert incremental_by_id["start"].bounds.y == 220
    assert incremental_by_id["process"].bounds.x == 948
    assert incremental_by_id["process"].bounds.y == 676
    # Incremental overrides are applied after ELK. Its original edge routes
    # must therefore be discarded; otherwise the visible paths keep pointing
    # at the pre-override coordinates and appear detached from their nodes.
    for edge in incremental_scene.edges:
        source = incremental_by_id[edge.source].bounds
        target = incremental_by_id[edge.target].bounds
        assert edge.points[0] == {
            "x": source.x + source.width,
            "y": source.y + source.height / 2,
        }
        assert edge.points[-1] == {
            "x": target.x,
            "y": target.y + target.height / 2,
        }

    preserve_value = _document()
    preserve_value["view"]["layoutMode"] = "preserve"
    preserve_value["view"]["overrides"] = {
        node["id"]: {
            "position": {"x": index * 200, "y": 100},
            "owner": "compiler",
        }
        for index, node in enumerate(preserve_value["model"]["nodes"])
    }
    preserve_document, issues = parse_and_validate(json.dumps(preserve_value))
    assert preserve_document is not None and issues == []
    preserve_scene = compile_diagram(preserve_document)
    assert [node.bounds.x for node in preserve_scene.nodes] == [0, 200, 400]


def test_preserve_layout_requires_every_node_position() -> None:
    value = _document()
    value["view"]["layoutMode"] = "preserve"
    value["view"]["overrides"] = {
        "start": {
            "position": {"x": 10, "y": 20},
            "owner": "compiler",
        },
    }

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None
    missing = [
        issue for issue in issues if issue.code == "preserve_position_missing"
    ]
    assert {issue.element_id for issue in missing} == {"process", "done"}


def test_relative_constraints_are_strict_and_drive_layout() -> None:
    value = _document()
    value["intent"]["constraints"] = [
        {"type": "same-rank", "elements": ["start", "process"]},
        {"type": "below", "element": "done", "target": "process"},
    ]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    by_id = {node.id: node.bounds for node in scene.nodes}
    assert by_id["start"].x == by_id["process"].x
    assert not (
        by_id["start"].y < by_id["process"].y + by_id["process"].height
        and by_id["start"].y + by_id["start"].height > by_id["process"].y
    )
    assert by_id["done"].y > by_id["process"].y + by_id["process"].height

    value["intent"]["constraints"][0]["pixelOffset"] = 42
    document, issues = parse_and_validate(json.dumps(value))
    assert document is None
    assert any(issue.code == "extra_forbidden" for issue in issues)


def test_constraint_references_are_source_addressable() -> None:
    value = _document()
    value["intent"]["constraints"] = [{
        "type": "route-above",
        "edge": "missing-edge",
        "element": "missing-node",
    }]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None
    by_code = {issue.code: issue for issue in issues}
    assert by_code["constraint_edge_not_found"].json_pointer == (
        "/intent/constraints/0/edge"
    )
    assert by_code["constraint_element_not_found"].json_pointer == (
        "/intent/constraints/0/element"
    )


def test_user_pin_wins_over_conflicting_layout_constraint() -> None:
    value = _document()
    value["view"]["overrides"] = {
        "process": {
            "position": {"x": 0, "y": 0},
            "pinned": True,
            "owner": "user",
        }
    }
    value["intent"]["constraints"] = [{
        "type": "right-of",
        "element": "process",
        "target": "done",
    }]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    process = next(node for node in scene.nodes if node.id == "process")
    assert process.bounds.x == 0
    assert any(issue.code == "constraint_unsatisfied" for issue in scene.issues)


def test_edge_router_avoids_unrelated_nodes_and_honors_route_side() -> None:
    value = _document()
    value["model"]["edges"].append({
        "id": "start-done",
        "source": "start",
        "target": "done",
        "kind": "flow",
    })
    value["intent"]["constraints"] = [{
        "type": "route-below",
        "edge": "start-done",
        "element": "process",
    }]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    direct = next(edge for edge in scene.edges if edge.id == "start-done")
    process = next(node for node in scene.nodes if node.id == "process")
    assert any(
        start["y"] == end["y"]
        and start["x"] != end["x"]
        and start["y"] > process.bounds.y + process.bounds.height
        for start, end in zip(direct.points, direct.points[1:])
    )
    assert not any(
        issue.code == "constraint_unsatisfied"
        for issue in scene.issues
    )
    assert not any(
        issue.code == "edge_routes_through_node"
        and issue.element_id == "start-done"
        for issue in scene.issues
    )


def test_forced_route_side_avoids_blockers_on_its_connector_legs() -> None:
    value = _document()
    value["model"]["edges"] = [{
        "id": "start-done",
        "source": "start",
        "target": "done",
        "kind": "flow",
    }]
    value["view"]["layoutMode"] = "preserve"
    value["view"]["overrides"] = {
        "start": {"position": {"x": 0, "y": 0}, "owner": "agent"},
        "process": {"position": {"x": 150, "y": 80}, "owner": "agent"},
        "done": {"position": {"x": 300, "y": 0}, "owner": "agent"},
    }
    value["intent"]["constraints"] = [{
        "type": "route-below",
        "edge": "start-done",
        "element": "process",
    }]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    direct = next(edge for edge in scene.edges if edge.id == "start-done")
    process = next(node for node in scene.nodes if node.id == "process")
    assert any(
        start["y"] == end["y"]
        and start["x"] != end["x"]
        and start["y"] > process.bounds.y + process.bounds.height
        for start, end in zip(direct.points, direct.points[1:])
    )
    assert not any(
        issue.code == "constraint_unsatisfied"
        for issue in scene.issues
    )
    assert not any(
        issue.code == "edge_routes_through_node"
        and issue.element_id == "start-done"
        for issue in scene.issues
    )


def test_explicit_pass_through_is_validated_and_not_reported_as_a_defect() -> None:
    value = _document()
    value["model"]["edges"].append({
        "id": "start-done",
        "source": "start",
        "target": "done",
        "kind": "flow",
    })
    value["view"]["layoutMode"] = "preserve"
    value["view"]["overrides"] = {
        "start": {"position": {"x": 0, "y": 100}, "owner": "agent"},
        "process": {"position": {"x": 240, "y": 100}, "owner": "agent"},
        "done": {"position": {"x": 480, "y": 100}, "owner": "agent"},
    }
    value["routingPolicy"] = {
        "allowPassThrough": [{"edge": "start-done", "element": "process"}],
    }

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    assert not any(
        issue.code == "edge_routes_through_node"
        and issue.element_ids == ["start-done", "process"]
        for issue in scene.issues
    )


def test_invalid_pass_through_rules_are_source_addressable() -> None:
    value = _document()
    value["routingPolicy"] = {
        "allowPassThrough": [
            {"edge": "missing-edge", "element": "missing-node"},
            {"edge": "start-process", "element": "start"},
            {"edge": "start-process", "element": "start"},
        ],
    }

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None
    by_code = {issue.code: issue for issue in issues}
    assert by_code["pass_through_edge_not_found"].json_pointer == (
        "/routingPolicy/allowPassThrough/0/edge"
    )
    assert by_code["pass_through_element_not_found"].json_pointer == (
        "/routingPolicy/allowPassThrough/0/element"
    )
    assert by_code["pass_through_endpoint_invalid"].json_pointer == (
        "/routingPolicy/allowPassThrough/2/element"
    )
    assert by_code["duplicate_pass_through_rule"].json_pointer == (
        "/routingPolicy/allowPassThrough/2"
    )


def test_inside_constraint_contributes_to_group_bounds() -> None:
    value = _document()
    value["model"]["groups"] = [{
        "id": "boundary",
        "label": "Boundary",
        "nodeIds": ["start"],
    }]
    value["intent"]["constraints"] = [{
        "type": "inside",
        "element": "process",
        "container": "boundary",
    }]

    document, issues = parse_and_validate(json.dumps(value))

    assert document is not None and issues == []
    scene = compile_diagram(document)
    group = scene.groups[0].bounds
    process = next(node for node in scene.nodes if node.id == "process").bounds
    assert group.x <= process.x
    assert group.x + group.width >= process.x + process.width


def test_registry_constraint_catalog_matches_authoring_schema() -> None:
    schema = DiagramDocument.model_json_schema(by_alias=True)
    serialized = json.dumps(schema, sort_keys=True)
    assert set(ALLOWED_CONSTRAINTS) == {
        "same-rank", "left-of", "right-of", "above", "below", "inside",
        "prefer-near", "prefer-apart", "order", "primary-path",
        "increase-gap", "route-above", "route-below",
    }
    assert all(constraint in serialized for constraint in ALLOWED_CONSTRAINTS)


def test_compile_rejects_oversized_canvas_and_expired_deadline() -> None:
    value = _document()
    value["view"]["overrides"] = {
        "done": {
            "position": {"x": MAX_CANVAS_EXTENT + 1000, "y": 0},
            "pinned": True,
            "owner": "user",
        }
    }
    document, issues = parse_and_validate(json.dumps(value))
    assert document is not None and issues == []

    with pytest.raises(DiagramLimitError, match="maximum supported") as extent:
        compile_diagram(document)
    assert extent.value.code == "canvas_bounds_exceeded"

    ordinary, issues = parse_and_validate(json.dumps(_document()))
    assert ordinary is not None and issues == []
    with pytest.raises(DiagramLimitError) as timeout:
        compile_diagram(ordinary, timeout_seconds=-1)
    assert timeout.value.code == "compile_timeout"


def test_review_renderer_enforces_shared_pixel_and_output_limits() -> None:
    document, issues = parse_and_validate(json.dumps(_document()))
    assert document is not None and issues == []
    scene = compile_diagram(document)
    png = render_scene_png(scene, max_width=2400, max_height=1600)
    image = Image.open(io.BytesIO(png))

    assert image.width * image.height <= MAX_REVIEW_PIXELS
    assert len(png) <= MAX_PNG_BYTES


def test_mcp_contracts_have_strict_composable_inputs_and_descriptions() -> None:
    expected = {
        "get_diagram_spec",
        "search_diagram_assets",
        "inspect_diagram",
        "check_diagram",
        "review_diagram",
        "read_diagram_review_image",
        "export_diagram",
    }
    assert set(DIAGRAM_TOOL_CONTRACTS) == expected
    # The signed present schema remains available only to resume historical
    # Turns; it is no longer advertised by the live tool/command catalog.
    assert set(DIAGRAM_INPUT_SCHEMAS) == {*expected, "present_diagram"}
    assert set(DIAGRAM_OUTPUT_SCHEMAS) == {*expected, "present_diagram"}
    for name in expected:
        Draft202012Validator.check_schema(DIAGRAM_INPUT_SCHEMAS[name])
        Draft202012Validator.check_schema(DIAGRAM_OUTPUT_SCHEMAS[name])
        description = DIAGRAM_TOOL_CONTRACTS[name].description
        assert all(
            heading in description
            for heading in (
                "Use when:",
                "Do not use:",
                "Input comes from:",
                "On success:",
                "On recoverable result:",
            )
        )


def test_check_input_rejects_unstructured_refs_and_extra_fields() -> None:
    validator = Draft202012Validator(DIAGRAM_INPUT_SCHEMAS["check_diagram"])
    spec = get_diagram_type("flow", "basic")
    assert spec is not None
    request = {
        "source_ref": {
            "path": "/memory/diagram-drafts/flow.vdiagram.json",
            "content_hash": "sha256:" + "a" * 64,
        },
        "spec_ref": {
            "schema_version": 1,
            "family": "flow",
            "type": "basic",
            "spec_version": "2026.08.1",
            "spec_hash": spec.spec_hash,
        },
    }
    validator.validate(request)

    request["source_ref"]["guessed_revision"] = "latest"
    with pytest.raises(ValidationError):
        validator.validate(request)
