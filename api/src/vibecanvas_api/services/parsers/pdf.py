"""PDF parser — pypdf, page-by-page text extraction with metadata.page."""
from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .base import (
    EmptyDocumentError,
    EncryptedDocumentError,
    ParsedSegment,
    ParseError,
    Parser,
)


class PdfParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        try:
            reader = PdfReader(BytesIO(blob))
        except PdfReadError as e:
            raise ParseError(f"Cannot parse PDF: {e}") from e

        if reader.is_encrypted:
            try:
                # pypdf returns 0 on failed decryption (no/wrong password).
                ok = reader.decrypt("")
            except Exception as e:
                raise EncryptedDocumentError(
                    f"PDF is password-protected: {e}"
                ) from e
            if ok == 0:
                raise EncryptedDocumentError("PDF is password-protected")

        segments: list[ParsedSegment] = []
        for idx, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = text.strip()
            if text:
                segments.append(
                    ParsedSegment(text=text, metadata={"page": idx})
                )

        if not segments:
            raise EmptyDocumentError(
                "PDF has no extractable text (likely image-only)"
            )
        return segments
