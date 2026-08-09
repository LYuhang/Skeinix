"""Parser protocol and ``ParsedSegment`` data model.

Parsers MUST be stateless: PARSER_REGISTRY stores CLASSES, callers
instantiate per-call. This makes concurrent indexer workers safe.
"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class ParsedSegment(BaseModel):
    """One natural segment of source document. Carries metadata that gets
    propagated to every chunk emitted from this segment.

    Metadata keys produced by each parser (T3 chunker propagates these
    opaquely into kb_chunks.chunk_metadata; T5 surfaces them in search
    results; V2 citation tracking depends on stable shapes):

    - ``page`` (int, 1-indexed): PDF page number.
    - ``heading`` (str): Markdown — last seen H1-H6 text before this segment.
    - ``section`` (str): DOCX — last seen Heading-styled paragraph text.
    - ``slide`` (int): PPTX slide number.
    - ``sheet`` + ``row_start`` / ``row_end``: XLSX location.
    - ``row`` (int): CSV/TSV row number.
    - ``json_path`` (str): JSON record path.
    - ``{}`` (empty): TXT, or heading-free document content.

    Future keys (V2): ``char_range``, ``page_range``, ``cell``. Producers
    MUST NOT collide with these reserved names.
    """
    text: str
    metadata: dict


class Parser(Protocol):
    """Stateless parser — instantiated per-call from PARSER_REGISTRY."""
    def parse(self, blob: bytes) -> list[ParsedSegment]: ...


class ParseError(Exception):
    """Generic parser failure. Subclasses signal specific reasons that the
    indexer maps to user-facing i18n messages."""


class EmptyDocumentError(ParseError):
    """Parsed segments are empty (e.g. image-only PDF) — V1 cannot OCR."""


class EncryptedDocumentError(ParseError):
    """Source is password-protected; cannot decrypt without user credentials."""
