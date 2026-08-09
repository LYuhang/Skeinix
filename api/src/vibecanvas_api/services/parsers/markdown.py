"""Markdown parser — header-aware segmentation with heading metadata."""
from __future__ import annotations

import re

import markdown as md_lib
from bs4 import BeautifulSoup, Tag

from .base import EmptyDocumentError, ParsedSegment, Parser


class MarkdownParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            text = blob.decode("utf-8", errors="replace")
        if not text.strip():
            raise EmptyDocumentError("empty markdown file")

        html = md_lib.markdown(text, extensions=["fenced_code", "tables"])
        soup = BeautifulSoup(html, "html.parser")

        segments: list[ParsedSegment] = []
        current_heading: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            body = "\n".join(buffer).strip()
            if body:
                meta: dict = {}
                if current_heading is not None:
                    meta["heading"] = current_heading
                segments.append(ParsedSegment(text=body, metadata=meta))
            buffer.clear()

        for elem in soup.children:
            if not isinstance(elem, Tag):
                continue
            if re.fullmatch(r"h[1-6]", elem.name):
                flush()
                current_heading = elem.get_text(strip=True)
            else:
                buffer.append(elem.get_text("\n", strip=True))
        flush()

        if not segments:
            raise EmptyDocumentError("markdown has no readable content")
        return segments
