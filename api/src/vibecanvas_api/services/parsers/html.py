"""HTML parser that preserves heading context and ignores active content."""
from __future__ import annotations

from bs4 import BeautifulSoup, Tag

from .base import EmptyDocumentError, ParsedSegment, Parser
from .txt import TxtParser


class HtmlParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        soup = BeautifulSoup(TxtParser._decode(blob), "html.parser")
        for tag in soup(["script", "style", "noscript", "template", "svg"]):
            tag.decompose()
        root = soup.body or soup
        segments: list[ParsedSegment] = []
        heading: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            body = "\n".join(part for part in buffer if part).strip()
            if body:
                metadata = {"heading": heading} if heading else {}
                segments.append(ParsedSegment(text=body, metadata=metadata))
            buffer.clear()

        for element in root.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "pre", "table"]):
            if not isinstance(element, Tag):
                continue
            text = element.get_text(" ", strip=True)
            if not text:
                continue
            if element.name and element.name.startswith("h"):
                flush()
                heading = text
            else:
                buffer.append(text)
        flush()
        if not segments:
            fallback = root.get_text("\n", strip=True)
            if fallback:
                segments.append(ParsedSegment(text=fallback, metadata={}))
        if not segments:
            raise EmptyDocumentError("HTML has no readable content")
        return segments
