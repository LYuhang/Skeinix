"""One compile contract shared by Diagram tools and Preview delivery."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from vibecanvas_api.diagrams.compiler import compile_diagram
from vibecanvas_api.diagrams.limits import DiagramLimitError
from vibecanvas_api.diagrams.models import DiagramDocument, DiagramScene
from vibecanvas_api.diagrams.validator import parse_and_validate


@dataclass(frozen=True, slots=True)
class DiagramRenderValidation:
    document: DiagramDocument | None
    scene: DiagramScene | None
    issues: tuple[dict[str, Any], ...]

    @property
    def ready(self) -> bool:
        return self.document is not None and self.scene is not None and not any(
            issue.get("severity") == "error"
            or issue.get("disposition") == "blocking"
            for issue in self.issues
        )


def validate_diagram_for_render(raw: bytes) -> DiagramRenderValidation:
    """Parse and compile exact bytes, preserving all Agent-actionable issues."""
    document, source_issues = parse_and_validate(raw)
    issues = [
        issue.model_dump(mode="json", by_alias=True)
        for issue in source_issues
    ]
    if document is None or any(issue.get("severity") == "error" for issue in issues):
        return DiagramRenderValidation(document, None, tuple(issues))
    try:
        scene = compile_diagram(document)
    except DiagramLimitError as exc:
        issues.append({
            "severity": "error",
            "disposition": "blocking",
            "stage": "compile",
            "code": exc.code,
            "json_pointer": "/view",
            "message": str(exc),
            "suggested_fix": (
                "Reduce diagram extent or complexity, then run check_diagram again."
            ),
        })
        return DiagramRenderValidation(document, None, tuple(issues))
    issues.extend(
        issue.model_dump(mode="json", by_alias=True)
        for issue in scene.issues
    )
    return DiagramRenderValidation(document, scene, tuple(issues))
