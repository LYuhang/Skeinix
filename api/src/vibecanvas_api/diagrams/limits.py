"""Shared resource limits for every VibeDiagram consumer.

Preview, MCP review, and export must fail the same document in the same way.
Keeping these values out of individual routes prevents one renderer from
silently accepting a payload another renderer cannot safely consume.
"""
from __future__ import annotations

import time

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_JSON_DEPTH = 16
MAX_CANVAS_EXTENT = 250_000.0
MAX_SCENE_BYTES = 8 * 1024 * 1024

COMPILE_TIMEOUT_SECONDS = 5.0
RENDER_TIMEOUT_SECONDS = 10.0

MAX_REVIEW_IMAGES = 3
MAX_REVIEW_WIDTH = 2400
MAX_REVIEW_HEIGHT = 1600
MAX_REVIEW_PIXELS = MAX_REVIEW_WIDTH * MAX_REVIEW_HEIGHT

MAX_SVG_BYTES = 16 * 1024 * 1024
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_PDF_BYTES = 32 * 1024 * 1024


class DiagramLimitError(ValueError):
    """A stable, source-addressable resource limit failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def deadline_after(seconds: float) -> float:
    return time.monotonic() + seconds


def check_deadline(deadline: float, *, operation: str) -> None:
    if time.monotonic() > deadline:
        raise DiagramLimitError(
            f"{operation}_timeout",
            f"Diagram {operation} exceeded its {operation} time limit.",
        )


def check_canvas_extent(width: float, height: float) -> None:
    if width > MAX_CANVAS_EXTENT or height > MAX_CANVAS_EXTENT:
        raise DiagramLimitError(
            "canvas_bounds_exceeded",
            "Compiled canvas exceeds the maximum supported width or height "
            f"of {MAX_CANVAS_EXTENT:g} canvas units.",
        )


def check_output_size(data: bytes, *, format: str) -> None:
    limits = {
        "svg": MAX_SVG_BYTES,
        "png": MAX_PNG_BYTES,
        "pdf": MAX_PDF_BYTES,
    }
    limit = limits[format]
    if len(data) > limit:
        raise DiagramLimitError(
            "diagram_output_too_large",
            f"Rendered {format.upper()} exceeds the {limit}-byte output limit.",
        )
