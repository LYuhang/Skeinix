"""CSV/TSV parser with row-aware citation metadata."""
from __future__ import annotations

import csv
from io import StringIO

from .base import EmptyDocumentError, ParsedSegment, ParseError, Parser
from .txt import TxtParser


class CsvParser(Parser):
    def parse(self, blob: bytes) -> list[ParsedSegment]:
        text = TxtParser._decode(blob)
        if not text.strip():
            raise EmptyDocumentError("empty delimited text file")
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",\t;|")
        except csv.Error:
            dialect = csv.excel
        try:
            rows = list(csv.reader(StringIO(text), dialect=dialect))
        except csv.Error as exc:
            raise ParseError(f"Cannot parse delimited text: {exc}") from exc
        nonempty = [row for row in rows if any(cell.strip() for cell in row)]
        if not nonempty:
            raise EmptyDocumentError("delimited text has no readable rows")
        headers = [cell.strip() or f"column_{index + 1}" for index, cell in enumerate(nonempty[0])]
        segments: list[ParsedSegment] = []
        for row_index, row in enumerate(nonempty[1:], start=2):
            values = [cell.strip() for cell in row]
            body = "\n".join(
                f"{headers[index] if index < len(headers) else f'column_{index + 1}'}: {value}"
                for index, value in enumerate(values)
                if value
            )
            if body:
                segments.append(ParsedSegment(text=body, metadata={"row": row_index}))
        if not segments:
            # A one-row CSV is still useful knowledge (for example a short list).
            body = "\n".join(value for value in headers if value)
            segments.append(ParsedSegment(text=body, metadata={"row": 1}))
        return segments
