"""DOCX parser — paragraph walking with Heading-style based sectioning."""
from __future__ import annotations

from io import BytesIO

from docx import Document as DocxDoc

from .archive import validate_office_archive
from .base import EmptyDocumentError, ParsedSegment, ParseError, Parser


class DocxParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        validate_office_archive(blob, required_prefix="word/")
        try:
            doc = DocxDoc(BytesIO(blob))
        except Exception as e:
            raise ParseError(f"Cannot parse docx: {e}") from e

        segments: list[ParsedSegment] = []
        current_section: str | None = None
        buffer: list[str] = []

        def flush() -> None:
            if not buffer:
                return
            body = "\n".join(buffer).strip()
            if body:
                meta: dict = {}
                if current_section is not None:
                    meta["section"] = current_section
                segments.append(ParsedSegment(text=body, metadata=meta))
            buffer.clear()

        for para in doc.paragraphs:
            txt = para.text or ""
            style = (para.style.name or "") if para.style else ""
            if style.startswith("Heading"):
                flush()
                current_section = txt.strip() or None
            elif txt.strip():
                buffer.append(txt)
        flush()

        if not segments:
            raise EmptyDocumentError("docx has no readable content")
        return segments
