from __future__ import annotations

import pytest

from vibecanvas_api.services.agent_runtime.instructions import (
    command_instructions_for_modes,
)


def test_command_instructions_are_resolved_once_by_backend_policy() -> None:
    instructions = command_instructions_for_modes(
        {"browser", "build"},
        activated_this_turn={"build"},
    )

    assert [item.name for item in instructions] == ["browser", "build"]
    assert instructions[0].activated_this_turn is False
    assert instructions[1].activated_this_turn is True
    assert "Browser mode" in instructions[0].content
    assert "BUILD mode" in instructions[1].content
    assert all(item.version == 1 for item in instructions)


def test_command_instruction_activation_must_be_active() -> None:
    with pytest.raises(ValueError, match="not active"):
        command_instructions_for_modes(
            {"build"},
            activated_this_turn={"browser"},
        )


def test_diagram_instruction_projects_exact_active_diagram_ref() -> None:
    diagram_ref = {
        "path": "/data/diagrams/system.vdiagram.json",
        "revision": "sha256:revision",
        "source_hash": "sha256:source",
        "bundle_hash": "sha256:source",
        "scene_ref": "scene://sha256:scene",
        "compiler_version": "1.0.0",
        "theme_version": "1.0.0",
    }
    active_context = {
        "diagram_ref": diagram_ref,
        "family": "architecture",
        "type": "system-container",
        "selected_element_ids": ["api"],
        "viewport_bounds": None,
    }
    instruction = command_instructions_for_modes(
        {"diagram"},
        active_diagram=active_context,
    )[0]
    assert '"revision": "sha256:revision"' in instruction.content
    assert '"family": "architecture"' in instruction.content
    assert '"selected_element_ids": ["api"]' in instruction.content
    assert '"editable_source_path": "/data/diagrams/system.vdiagram.json"' in instruction.content


def test_diagram_instruction_accepts_legacy_direct_ref() -> None:
    diagram_ref = {
        "path": "/data/diagrams/legacy.vdiagram.json",
        "revision": "sha256:revision",
        "source_hash": "sha256:source",
        "bundle_hash": "sha256:source",
        "scene_ref": "scene://sha256:scene",
        "compiler_version": "1.0.0",
        "theme_version": "1.0.0",
    }
    instruction = command_instructions_for_modes(
        {"diagram"}, active_diagram=diagram_ref
    )[0]
    assert '"diagram_ref": {' in instruction.content
    assert '"editable_source_path": "/data/diagrams/legacy.vdiagram.json"' in instruction.content
