"""Versioned visual tokens shared by Preview and server export.

The browser's JSON copy is checked against this mapping in the diagram golden
suite. Keeping the server mapping embedded makes wheel/container installs
self-contained while the cross-surface test prevents silent drift.
"""
from __future__ import annotations

from typing import Literal

DiagramTheme = Literal["light", "dark", "print"]
THEME_VERSION = "1.0.0"

DIAGRAM_THEME_PALETTES: dict[DiagramTheme, dict] = {
    "light": {
        "background": "#fafbfd", "foreground": "#20242a",
        "secondary": "#626a76", "border": "#c9ced6", "edge": "#7a8290",
        "roleFills": {
            "primary": "#eef2ff", "secondary": "#f5f6f8",
            "service": "#f3f5ff", "data": "#edf8f2", "storage": "#edf8f2",
            "external": "#f5f6f8", "actor": "#f5f6f8", "warning": "#fff8e8",
            "danger": "#fff1f1", "success": "#edf8f2", "event": "#f4f0ff",
            "note": "#fffdf2", "neutral": "#ffffff",
        },
    },
    "dark": {
        "background": "#17191d", "foreground": "#f2f3f5",
        "secondary": "#aab0ba", "border": "#555d68", "edge": "#9098a5",
        "roleFills": {
            "primary": "#29283a", "secondary": "#252930",
            "service": "#252a38", "data": "#21322c", "storage": "#21322c",
            "external": "#252930", "actor": "#252930", "warning": "#352f20",
            "danger": "#382527", "success": "#21322c", "event": "#2d2739",
            "note": "#332f22", "neutral": "#252930",
        },
    },
    "print": {
        "background": "#ffffff", "foreground": "#111827",
        "secondary": "#4b5563", "border": "#9ca3af", "edge": "#6b7280",
        "roleFills": {
            "primary": "#f8fafc", "secondary": "#ffffff",
            "service": "#f8fafc", "data": "#f8fafc", "storage": "#f8fafc",
            "external": "#ffffff", "actor": "#ffffff", "warning": "#fffbeb",
            "danger": "#fff7f7", "success": "#f7fcf9", "event": "#faf8ff",
            "note": "#fffdf4", "neutral": "#ffffff",
        },
    },
}


def diagram_palette(theme: str) -> dict:
    """Return a validated palette; unknown values fail closed to light."""
    return DIAGRAM_THEME_PALETTES.get(theme, DIAGRAM_THEME_PALETTES["light"])
