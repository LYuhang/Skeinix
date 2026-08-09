"""Parser registry — store CLASSES, not instances (stateless invariant).

Callers MUST instantiate per-call: ``PARSER_REGISTRY[t]()``. This keeps
indexer workers safe to run in parallel — no shared per-parser state.
"""
from __future__ import annotations

from .base import (
    EmptyDocumentError,
    EncryptedDocumentError,
    ParsedSegment,
    ParseError,
    Parser,
)
from .csv import CsvParser
from .docx import DocxParser
from .html import HtmlParser
from .json import JsonParser
from .markdown import MarkdownParser
from .pdf import PdfParser
from .pptx import PptxParser
from .txt import TxtParser
from .xlsx import XlsxParser

PARSER_REGISTRY: dict[str, type[Parser]] = {
    "pdf": PdfParser,
    "docx": DocxParser,
    "pptx": PptxParser,
    "xlsx": XlsxParser,
    "csv": CsvParser,
    "json": JsonParser,
    "html": HtmlParser,
    "markdown": MarkdownParser,
    "txt": TxtParser,
}

SUPPORTED_FILE_EXTENSIONS = (
    ".pdf", ".docx", ".pptx", ".xlsx", ".csv", ".tsv",
    ".json", ".html", ".htm", ".md", ".markdown", ".txt",
    ".log", ".rst",
)


def detect_parser_type(filename: str, mime_type: str) -> str | None:
    """Return parser_type or None for unsupported.

    Checks BOTH filename extension AND MIME — mismatch returns None
    (defeats ``evil.pdf`` carrying docx bytes attacks).
    """
    name = (filename or "").lower()
    mime = (mime_type or "").lower()
    if name.endswith(".pdf") and "pdf" in mime:
        return "pdf"
    if name.endswith(".docx") and (
        "officedocument" in mime or "msword" in mime
    ):
        return "docx"
    if name.endswith(".pptx") and (
        "presentationml" in mime or "officedocument" in mime
    ):
        return "pptx"
    if name.endswith(".xlsx") and (
        "spreadsheetml" in mime or "officedocument" in mime
    ):
        return "xlsx"
    if (name.endswith(".csv") or name.endswith(".tsv")) and (
        "csv" in mime or "tab-separated" in mime or "text" in mime
    ):
        return "csv"
    if name.endswith(".json") and ("json" in mime or "text" in mime):
        return "json"
    if (name.endswith(".html") or name.endswith(".htm")) and (
        "html" in mime or "text" in mime
    ):
        return "html"
    if (name.endswith(".md") or name.endswith(".markdown")) and (
        "markdown" in mime or "text" in mime
    ):
        return "markdown"
    if name.endswith((".txt", ".log", ".rst")) and "text" in mime:
        return "txt"
    return None


__all__ = [
    "PARSER_REGISTRY",
    "SUPPORTED_FILE_EXTENSIONS",
    "detect_parser_type",
    "Parser",
    "ParsedSegment",
    "ParseError",
    "EmptyDocumentError",
    "EncryptedDocumentError",
]
