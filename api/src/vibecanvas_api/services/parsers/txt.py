"""TXT parser — UTF-8 / UTF-8-BOM / GBK / latin-1 fallback chain."""
from __future__ import annotations

from .base import EmptyDocumentError, ParsedSegment, Parser


class TxtParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        if not blob:
            raise EmptyDocumentError("empty TXT file")
        text = self._decode(blob)
        if not text.strip():
            raise EmptyDocumentError("TXT has no printable content")
        return [ParsedSegment(text=text, metadata={})]

    @staticmethod
    def _decode(blob: bytes) -> str:
        for enc in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
            try:
                return blob.decode(enc)
            except UnicodeDecodeError:
                continue
        return blob.decode("utf-8", errors="replace")
