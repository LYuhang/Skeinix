"""Markdown parser tests — heading metadata + code block preservation."""
from __future__ import annotations

from vibecanvas_api.services.parsers.markdown import MarkdownParser


def test_markdown_headings_propagate():
    md = b"""# Top

paragraph A

## Section 1

paragraph B
"""
    segments = MarkdownParser().parse(md)
    # At least one segment with heading metadata for each level.
    assert any(s.metadata.get("heading") == "Top" for s in segments)
    assert any(s.metadata.get("heading") == "Section 1" for s in segments)
    full = " ".join(s.text for s in segments)
    assert "paragraph A" in full
    assert "paragraph B" in full


def test_markdown_code_block_preserved():
    md = b"# T\n\n```python\nprint('x')\n```\n"
    segments = MarkdownParser().parse(md)
    assert any("print('x')" in s.text for s in segments)
