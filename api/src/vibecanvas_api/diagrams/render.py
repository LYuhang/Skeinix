"""Server renderers consuming the same Scene IR as browser Preview."""
from __future__ import annotations

import html
import io
import math
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from .limits import (
    MAX_REVIEW_HEIGHT,
    MAX_REVIEW_PIXELS,
    MAX_REVIEW_WIDTH,
    RENDER_TIMEOUT_SECONDS,
    DiagramLimitError,
    check_canvas_extent,
    check_deadline,
    check_output_size,
    deadline_after,
)
from .models import DiagramScene, SceneBounds
from .visual_tokens import diagram_palette

MAX_RENDER_WIDTH = MAX_REVIEW_WIDTH
MAX_RENDER_HEIGHT = MAX_REVIEW_HEIGHT
_FONT_CANDIDATES = (
    # The review fixtures and common product prompts contain Chinese. Keep a
    # CJK-capable font first; the API image installs this exact Debian asset.
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
_FONT_PATH = next(
    (path for path in _FONT_CANDIDATES if Path(path).is_file()),
    _FONT_CANDIDATES[-1],
)
_SVG_FONT_FAMILY = "WenQuanYi Zen Hei, DejaVu Sans, sans-serif"

# Scene bounds describe semantic geometry. Export renderers also draw arrow
# heads and labels outside that geometry, so keep a stable visual bleed around
# every side instead of clipping content that happens to sit on an outer edge.
_RENDER_PADDING = 24.0


def _render_bounds(scene: DiagramScene) -> SceneBounds:
    bounds = scene.bounds
    return SceneBounds(
        x=bounds.x - _RENDER_PADDING,
        y=bounds.y - _RENDER_PADDING,
        width=bounds.width + 2 * _RENDER_PADDING,
        height=bounds.height + 2 * _RENDER_PADDING,
    )


def _transform(scene: DiagramScene, max_width: int, max_height: int):
    bounds = _render_bounds(scene)
    scale = min(max_width / max(1, bounds.width), max_height / max(1, bounds.height), 2.0)
    width = max(320, min(max_width, round(bounds.width * scale)))
    height = max(220, min(max_height, round(bounds.height * scale)))

    def point(x: float, y: float) -> tuple[float, float]:
        return ((x - bounds.x) * scale, (y - bounds.y) * scale)

    return scale, width, height, point


def render_scene_png(
    scene: DiagramScene,
    *,
    theme: Literal["light", "dark", "print"] = "light",
    max_width: int = 1600,
    max_height: int = 1000,
    background: Literal["transparent", "white", "theme"] = "theme",
) -> bytes:
    deadline = deadline_after(RENDER_TIMEOUT_SECONDS)
    check_canvas_extent(scene.bounds.width, scene.bounds.height)
    max_width = min(MAX_RENDER_WIDTH, max(320, max_width))
    max_height = min(MAX_RENDER_HEIGHT, max(220, max_height))
    if max_width * max_height > MAX_REVIEW_PIXELS:
        raise DiagramLimitError(
            "review_pixels_exceeded",
            "Requested diagram image exceeds the review pixel limit.",
        )
    scale, width, height, point = _transform(scene, max_width, max_height)
    check_deadline(deadline, operation="render")
    palette = diagram_palette(theme)
    background_color = (
        "#ffffff"
        if background == "white"
        else palette["background"]
    )
    foreground = palette["foreground"]
    secondary = palette["secondary"]
    border = palette["border"]
    edge_color = palette["edge"]
    image_mode = "RGBA" if background == "transparent" else "RGB"
    image_background = (
        (0, 0, 0, 0) if background == "transparent" else background_color
    )
    image = Image.new(image_mode, (width, height), image_background)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(_FONT_PATH, max(11, round(13 * min(scale, 1.25))))
    small = ImageFont.truetype(_FONT_PATH, max(9, round(10 * min(scale, 1.25))))
    for group in scene.groups:
        check_deadline(deadline, operation="render")
        x, y = point(group.bounds.x, group.bounds.y)
        x2, y2 = point(group.bounds.x + group.bounds.width, group.bounds.y + group.bounds.height)
        draw.rounded_rectangle((x, y, x2, y2), radius=12, outline=border, width=1)
        draw.text((x + 10, y + 8), group.label, fill=secondary, font=small)
    for edge in scene.edges:
        check_deadline(deadline, operation="render")
        points = [point(item["x"], item["y"]) for item in edge.points]
        draw.line(points, fill=edge_color, width=max(1, round(1.5 * scale)), joint="curve")
        if len(points) >= 2:
            end, previous = points[-1], points[-2]
            delta_x, delta_y = end[0] - previous[0], end[1] - previous[1]
            length = max(1.0, math.hypot(delta_x, delta_y))
            unit_x, unit_y = delta_x / length, delta_y / length
            base_x, base_y = end[0] - 8 * unit_x, end[1] - 8 * unit_y
            draw.polygon([
                end,
                (base_x - 4 * unit_y, base_y + 4 * unit_x),
                (base_x + 4 * unit_y, base_y - 4 * unit_x),
            ], fill=edge_color)
        if edge.label:
            middle = points[len(points) // 2]
            draw.text((middle[0] + 4, middle[1] - 14), edge.label, fill=secondary, font=small)
    for node in scene.nodes:
        check_deadline(deadline, operation="render")
        x, y = point(node.bounds.x, node.bounds.y)
        x2, y2 = point(node.bounds.x + node.bounds.width, node.bounds.y + node.bounds.height)
        fill = palette["roleFills"].get(node.style_role, palette["roleFills"]["neutral"])
        draw.rounded_rectangle((x, y, x2, y2), radius=8, fill=fill, outline=border, width=max(1, round(scale)))
        label_y = y + 11
        for line in node.label_lines:
            draw.text((x + 12, label_y), line, fill=foreground, font=font)
            label_y += 18
        description_y = label_y + 4
        for line in node.description_lines:
            draw.text(
                (x + 12, description_y),
                line,
                fill=secondary,
                font=small,
            )
            description_y += 16
        for port in node.ports:
            port_x, port_y = point(port.x, port.y)
            radius = max(2, round(3 * scale))
            draw.ellipse(
                (
                    port_x - radius,
                    port_y - radius,
                    port_x + radius,
                    port_y + radius,
                ),
                fill=foreground,
            )
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    data = output.getvalue()
    check_deadline(deadline, operation="render")
    check_output_size(data, format="png")
    return data


def render_scene_svg(
    scene: DiagramScene,
    *,
    theme: str = "light",
    background: Literal["transparent", "white", "theme"] = "theme",
) -> bytes:
    deadline = deadline_after(RENDER_TIMEOUT_SECONDS)
    bounds = _render_bounds(scene)
    check_canvas_extent(bounds.width, bounds.height)
    palette = diagram_palette(theme)
    background_color = (
        "#ffffff"
        if background == "white"
        else palette["background"]
    )
    foreground = palette["foreground"]
    secondary = palette["secondary"]
    border = palette["border"]
    edge_color = palette["edge"]
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{bounds.x} '
            f'{bounds.y} {bounds.width} {bounds.height}" role="img" '
            f'aria-label="{html.escape(scene.title)}">'
        ),
    ]
    if background != "transparent":
        parts.append(
            f'<rect x="{bounds.x}" y="{bounds.y}" width="{bounds.width}" '
            f'height="{bounds.height}" fill="{background_color}"/>'
        )
    parts.append(

            '<defs><marker id="arrow" markerWidth="8" markerHeight="8" '
            'refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 '
            f'L0,8 z" fill="{edge_color}"/></marker></defs>'

    )
    for group in scene.groups:
        check_deadline(deadline, operation="render")
        b = group.bounds
        parts.append(
            f'<rect x="{b.x}" y="{b.y}" width="{b.width}" '
            f'height="{b.height}" rx="12" fill="none" stroke="{border}" '
            'stroke-dasharray="6 5"/>'
        )
        parts.append(
            f'<text x="{b.x + 12}" y="{b.y + 20}" '
            f'font-family="{_SVG_FONT_FAMILY}" font-size="11" '
            f'fill="{secondary}">{html.escape(group.label)}</text>'
        )
    for edge in scene.edges:
        check_deadline(deadline, operation="render")
        points = " ".join(f'{item["x"]},{item["y"]}' for item in edge.points)
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{edge_color}" '
            'stroke-width="1.5" marker-end="url(#arrow)"/>'
        )
        if edge.label:
            middle = edge.points[len(edge.points) // 2]
            parts.append(
                f'<text x="{middle["x"] + 4}" y="{middle["y"] - 7}" '
                f'font-family="{_SVG_FONT_FAMILY}" font-size="10" '
                f'fill="{secondary}">{html.escape(edge.label)}</text>'
            )
    for node in scene.nodes:
        check_deadline(deadline, operation="render")
        b = node.bounds
        fill = palette["roleFills"].get(node.style_role, palette["roleFills"]["neutral"])
        parts.append(
            f'<g data-element-id="{html.escape(node.id)}"><rect x="{b.x}" '
            f'y="{b.y}" width="{b.width}" height="{b.height}" rx="8" '
            f'fill="{fill}" stroke="{border}"/>'
        )
        label_spans = "".join(
            f'<tspan x="{b.x + 12}" dy="{0 if index == 0 else 18}">'
            f'{html.escape(line)}</tspan>'
            for index, line in enumerate(node.label_lines)
        )
        parts.append(
            f'<text x="{b.x + 12}" y="{b.y + 25}" '
            f'font-family="{_SVG_FONT_FAMILY}" font-size="13" '
            f'font-weight="600" fill="{foreground}">{label_spans}</text>'
        )
        if node.description_lines:
            description_y = b.y + 29 + len(node.label_lines) * 18
            description_spans = "".join(
                f'<tspan x="{b.x + 12}" dy="{0 if index == 0 else 16}">'
                f'{html.escape(line)}</tspan>'
                for index, line in enumerate(node.description_lines)
            )
            parts.append(
                f'<text x="{b.x + 12}" y="{description_y}" '
                f'font-family="{_SVG_FONT_FAMILY}" font-size="10" '
                f'fill="{secondary}">{description_spans}</text>'
            )
        for port in node.ports:
            parts.append(
                f'<circle cx="{port.x}" cy="{port.y}" r="3" '
                f'fill="{foreground}" data-port-id="{html.escape(port.id)}"/>'
            )
        parts.append("</g>")
    parts.append("</svg>")
    data = "".join(parts).encode()
    check_deadline(deadline, operation="render")
    check_output_size(data, format="svg")
    return data


def render_scene_pdf(
    scene: DiagramScene,
    *,
    theme: str = "print",
    background: Literal["transparent", "white", "theme"] = "white",
) -> bytes:
    deadline = deadline_after(RENDER_TIMEOUT_SECONDS)
    png = render_scene_png(
        scene,
        theme=theme,
        max_width=2400,
        max_height=1600,
        # PDF has no useful transparent-page contract across viewers.
        background="white" if background == "transparent" else background,
    )
    image = Image.open(io.BytesIO(png)).convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PDF", resolution=144.0)
    data = output.getvalue()
    check_deadline(deadline, operation="render")
    check_output_size(data, format="pdf")
    return data
