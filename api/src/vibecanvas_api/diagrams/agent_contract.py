"""Generate the compact /diagram Agent contract from the live Registry."""
from __future__ import annotations

from dataclasses import dataclass

from .models import DiagramDocument
from .registry import REGISTRY_VERSION, list_enabled_types

AGENT_CONTRACT_VERSION = REGISTRY_VERSION


@dataclass(frozen=True, slots=True)
class DiagramToolContract:
    stage: str
    use_when: str
    do_not_use_when: str
    input_comes_from: str
    on_success: str
    on_recoverable_result: str

    @property
    def description(self) -> str:
        return "\n".join((
            f"Use when: {self.use_when}",
            f"Do not use: {self.do_not_use_when}",
            f"Input comes from: {self.input_comes_from}",
            f"On success: {self.on_success}",
            f"On recoverable result: {self.on_recoverable_result}",
        ))


DIAGRAM_TOOL_CONTRACTS: dict[str, DiagramToolContract] = {
    "get_diagram_spec": DiagramToolContract(
        stage="spec",
        use_when="one exact enabled family/type has been selected from the command catalog.",
        do_not_use_when="requesting several types, an unlisted type, or relying on a remembered schema.",
        input_comes_from="the enabled type catalog injected by /diagram.",
        on_success="write a complete draft from authoring_schema, instructions and minimal_example; copy spec_ref to check_diagram.",
        on_recoverable_result="refresh the command catalog or report that the requested type is unavailable.",
    ),
    "search_diagram_assets": DiagramToolContract(
        stage="assets",
        use_when="the selected spec requires a professional platform symbol.",
        do_not_use_when="ordinary semantic shapes are sufficient.",
        input_comes_from="the selected exact family/type and the user's symbol intent.",
        on_success="copy an asset_key unchanged into a compatible node assetRef.",
        on_recoverable_result="use a spec-allowed semantic fallback; never invent an asset key.",
    ),
    "inspect_diagram": DiagramToolContract(
        stage="inspect",
        use_when="modifying the active diagram or resolving a referenced element.",
        do_not_use_when="only a path/latest value is available or a full source read is required.",
        input_comes_from="the trusted Active Diagram Context diagram_ref.",
        on_success=(
            "seed the smallest draft edit from the canonical source; copy "
            "next.retained_layout into view so retained IDs keep their "
            "compiler/user-owned positions."
        ),
        on_recoverable_result="narrow the selector or reload the current active ref after a conflict.",
    ),
    "check_diagram": DiagramToolContract(
        stage="check",
        use_when="a complete auto-saved /data/diagrams file and exact spec_ref are available.",
        do_not_use_when="the JSON write is incomplete, its hash is stale, or no exact spec_ref exists.",
        input_comes_from="the latest filesystem write plus get_diagram_spec; base ref comes from inspect/active context.",
        on_success="copy render_request unchanged to render_interactive; continue editing after coherent intermediate checks.",
        on_recoverable_result="edit issues by json_pointer or reread the current file, then check again.",
    ),
    "review_diagram": DiagramToolContract(
        stage="review",
        use_when="an initial, major or final exact presented revision must be visually verified.",
        do_not_use_when="only a draft/path-latest value exists.",
        input_comes_from="the exact diagram_ref returned by check_diagram.",
        on_success=(
            "use structured issues first; when image_delivery says evidence is "
            "needed, read the referenced sandbox image with the Runtime's own "
            "image tool. Obey next.action and deliver only on deliver."
        ),
        on_recoverable_result=(
            "when next.action is edit_source, do not answer or stop: repair the "
            "reported pointers and run a bounded edit/check/present/review loop."
        ),
    ),
    "read_diagram_review_image": DiagramToolContract(
        stage="review-image",
        use_when=(
            "the user requested visual confirmation or structured Review "
            "evidence is insufficient and the Runtime has no native image tool."
        ),
        do_not_use_when=(
            "the path did not come from the latest review_images entry or a "
            "native Runtime image tool already completed the same inspection."
        ),
        input_comes_from=(
            "the exact latest review_images sandbox_path returned by "
            "review_diagram."
        ),
        on_success=(
            "inspect the attached image content and answer the concrete visual "
            "question before reporting completion."
        ),
        on_recoverable_result=(
            "rerun review_diagram for the latest revision and copy its current "
            "sandbox_path unchanged."
        ),
    ),
    "export_diagram": DiagramToolContract(
        stage="export",
        use_when="the user or delivery workflow explicitly needs an SVG, PNG or PDF artifact.",
        do_not_use_when="the source is a draft/path-latest or export was not requested.",
        input_comes_from="the final exact presented diagram_ref and explicit delivery options.",
        on_success="deliver download_ref to the UI/user without treating it as new diagram source.",
        on_recoverable_result="choose a declared supported format or safe basename and retry.",
    ),
}

DIAGRAM_WORKFLOWS = {
    "create": (
        "select_enabled_type",
        "get_diagram_spec",
        "filesystem_write",
        "check_diagram",
        "render_interactive",
        "review_diagram",
    ),
    "modify": (
        "active_diagram_ref",
        "inspect_diagram",
        "filesystem_edit",
        "check_diagram",
        "render_interactive",
        "review_diagram",
    ),
}


def diagram_tool_description(name: str) -> str:
    return DIAGRAM_TOOL_CONTRACTS[name].description


def tool_stage_catalog() -> str:
    return "\n".join(
        f"- {name} [{contract.stage}]: {contract.use_when}"
        for name, contract in DIAGRAM_TOOL_CONTRACTS.items()
    )


def enabled_type_catalog() -> str:
    return "\n".join(
        f"- {item.key} — {item.use_when} Do not use when: {'; '.join(item.do_not_use_when)}"
        for item in list_enabled_types()
    )


def base_schema_overview() -> str:
    """Project the live Base Schema root instead of duplicating its fields."""
    return ", ".join(
        field.alias or name
        for name, field in DiagramDocument.model_fields.items()
    )


def diagram_agent_prompt() -> str:
    return f"""You are in additive Diagram mode. Author semantic diagrams through the
VibeDiagram protocol; do not hand-author renderer geometry or claim unsupported
diagram types.

VibeDiagram file ownership:
- Base Schema root fields (generated): {base_schema_overview()}.
- diagram selects exactly one registered family/type.
- model contains nodes, edges, groups, embeds and resources: what exists and relates.
- intent contains direction, density, primaryPath and relative constraints: how the compiler organizes meaning.
- view contains layoutMode, frames and presentation overrides. Preserve user-owned pinned overrides.
- metadata binds the exact specVersion/specHash and compiler/theme versions.

Capabilities in this build:
- Registry: {REGISTRY_VERSION}; Agent contract: {AGENT_CONTRACT_VERSION}.
- Enabled type catalog:
{enabled_type_catalog()}
- Tool stage catalog:
{tool_stage_catalog()}

CREATE workflow:
1. Select exactly one enabled family/type; ask one focused question only when
   ambiguity materially changes the information shown.
2. Call get_diagram_spec before writing and follow its full authoring_schema and minimal_example.
3. Use search_diagram_assets only when professional symbols are needed.
4. Write the complete source at exactly
   /data/diagrams/<slug>.vdiagram.json (the `.vdiagram.json` suffix is
   required) with existing filesystem tools. This auto-saved file is the
   single source of truth; do not create a separate draft or publish copy.
5. For a complex diagram, finish one coherent semantic operation at a time
   (for example add a node, then an edge). After each operation write the whole
   valid source and call check_diagram(validation_level=compile).
   Never write half JSON merely to animate it.
   Repair reported JSON pointers before continuing.
6. On the final complete operation call check_diagram explicitly, even though
   render_interactive repeats validation as a delivery safety check. Copy the
   returned render_request unchanged to render_interactive. A validation error
   must be repaired and must not produce a Preview card.
7. Pass the exact returned diagram_ref to review_diagram. Use structured issues first;
   when geometry is ambiguous, global hierarchy matters, or pixel confirmation
   is needed, call the current Runtime's image-reading tool on the returned
   review_images sandbox_path. Do not claim pixel inspection unless that call
   actually succeeded.

MODIFY workflow:
1. Use the exact active DiagramRef and call inspect_diagram; never guess path or revision.
2. Read and edit exactly inspect_diagram.next.write_source_path in place. A
   modification is an auto-saved revision of the same canonical path: never add a suffix, create a
   second diagram tab, or silently fork the diagram. An intentional copy is a
   separate create operation and must use a new diagram id.
3. Copy
   inspect_diagram.next.retained_layout.layout_mode to view.layoutMode and its
   overrides for every retained element into view.overrides. Preserve user
   overrides exactly and remove a returned override only when its element is
   intentionally deleted. Never leave a modification in auto layout.
4. Preserve stable IDs, user pins and unrelated content, then make the smallest
   semantic change. Never repurpose an unrelated existing node to satisfy a new
   requested node. Only new elements are placed by incremental layout.
5. Before the final check, reconcile the resulting source against every atomic
   user change: additions, removals/replacements, preserved elements, labels
   and relationships. A request to split, replace or merge an element consumes
   the original element unless the user explicitly says to retain it. Visual
   review validates rendering and cannot substitute for this semantic check.
6. When calling check_diagram, pass removed_element_ids containing only exact
   stable IDs the user explicitly asked to delete or replace; omit every retained
   ID. An empty list protects every base node.
7. For multi-operation edits, write and check each complete intermediate source.
   On the final source run check -> render_interactive -> review.

Completion rules:
- Treat every ref, hash and revision as opaque; copy returned objects unchanged.
- A tool validation error never creates a ref. Correct only the rejected
  argument and retry that same stage; never invent a DiagramRef,
  scene_ref, revision, path suffix, hash, or version.
- Treat every tool `next.action` as mandatory control flow, not advice. In
  particular, after review_diagram returns `edit_source`, do not answer the user
  or stop: repair the reported source pointers and rerun check -> render ->
  review until the latest review returns `deliver` or the 3-round limit is
  reached.
- The /data/diagrams file is the canonical editable source and is auto-saved.
- Never bypass a stale hash, revision conflict or visual issue.
- Run no more than 3 visual review rounds; disclose remaining non-blocking warnings.
- `accepted` and `render_cue` issues are non-actionable. Do not edit source only
  to eliminate them; render cues such as gap/bridge resolve their ambiguity.
- A `deliver` review proves structural readiness, not that you saw its pixels.
  If the user asks you to view the rendered image or asks a question whose
  answer depends on it, call the Runtime image-reading tool on the latest
  review_images sandbox_path, or use read_diagram_review_image when the Runtime
  has no native image tool, and include the concrete visual answer. A generic
  completion statement does not satisfy that request.
- Report completion only for the latest checked file after review returns deliver.
- Export only when requested or required for delivery."""
