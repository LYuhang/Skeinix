"""Safety checks shared by ZIP-based Office document parsers."""
from __future__ import annotations

from io import BytesIO
from zipfile import BadZipFile, ZipFile

from .base import ParseError


MAX_ARCHIVE_ENTRIES = 10_000
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 250


def validate_office_archive(blob: bytes, *, required_prefix: str) -> None:
    """Reject malformed and expansion-bomb Office archives before parsing."""
    try:
        with ZipFile(BytesIO(blob)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_ENTRIES:
                raise ParseError("Office document contains too many archive entries")
            names = {entry.filename for entry in entries}
            if "[Content_Types].xml" not in names or not any(
                name.startswith(required_prefix) for name in names
            ):
                raise ParseError("Office document has an invalid package structure")
            expanded = sum(entry.file_size for entry in entries)
            compressed = sum(entry.compress_size for entry in entries)
            if expanded > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise ParseError("Office document expands beyond the safety limit")
            if compressed and expanded / compressed > MAX_ARCHIVE_COMPRESSION_RATIO:
                raise ParseError("Office document compression ratio exceeds the safety limit")
            if any(entry.flag_bits & 0x1 for entry in entries):
                raise ParseError("Encrypted Office documents are not supported")
    except BadZipFile as exc:
        raise ParseError("Office document is not a valid ZIP package") from exc
