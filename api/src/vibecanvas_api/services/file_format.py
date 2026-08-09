"""Single source of truth for file-format determination — content_type mapping
and text/binary detection — shared by the agent fs tools (read_file / write_file /
grep) and the sandbox/storage services (fileops, vfs_run_context).

``content_type`` drives two real consumers, so it must be the SAME for a given file
on every path:
  * compaction routing — ``agents/middleware/compaction_policy.py`` keys
    ``fresh_k`` / ``priority`` / ``aged_form`` on it;
  * frontend file serving — ``routes/vfs.py`` decides text-vs-binary inline and the
    HTTP ``Content-Type`` from it.
Keeping ONE map here means the type a file gets on READ equals the type it gets on
STORE, so those two consumers never disagree for the same file.

Pure stdlib (``os`` / ``mimetypes``) so it is safe to import INSIDE the sandbox
(``fileops`` runs in-sandbox).
"""
from __future__ import annotations

import mimetypes
import os

# Curated extension → content_type map, checked FIRST (before mimetypes) so the
# ``table/*`` and ``text/*`` subtypes the compaction registry + the frontend rely
# on survive. Union of the former ``read_file._EXT_CT`` (text-rich) and
# ``vfs_run_context._CT_BY_EXT`` (binary-rich).
_CONTENT_TYPE_BY_EXT: dict[str, str] = {
    # text
    ".txt": "text/plain", ".log": "text/plain",
    ".yaml": "text/plain", ".yml": "text/plain",
    ".md": "text/markdown",
    ".py": "text/python",
    ".sh": "text/shell",
    ".html": "text/html", ".htm": "text/html",
    ".json": "application/json",
    # tables (line-oriented; compaction + frontend treat these as inline text)
    ".csv": "table/csv", ".tsv": "table/tsv", ".jsonl": "table/jsonl",
    # images
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif",
    # audio / documents
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".pdf": "application/pdf",
}

_BINARY_SNIFF_BYTES = 8192


def content_type_for(path: str, data: bytes | None = None) -> str:
    """The canonical content_type for ``path``.

    (1) the curated extension map FIRST (so ``table/*`` + text subtypes survive);
    (2) else ``mimetypes.guess_type``;
    (3) else, when ``data`` is given, a NUL-byte sniff — ``application/octet-stream``
        for binary, ``text/plain`` for printable; with no ``data``, ``text/plain``.
    """
    ext = os.path.splitext(path)[1].lower()
    if ext in _CONTENT_TYPE_BY_EXT:
        return _CONTENT_TYPE_BY_EXT[ext]
    guessed, _ = mimetypes.guess_type(path)
    if guessed:
        return guessed
    if data is not None and is_binary_bytes(data):
        return "application/octet-stream"
    return "text/plain"


def is_text_content_type(ct: str | None) -> bool:
    """Whether a content_type holds text we can read / search as lines."""
    ct = ct or ""
    return (ct.startswith("text/") or ct == "application/json"
            or ct.startswith("table/") or ct in ("json", "text"))


def is_binary_bytes(data: bytes) -> bool:
    """True iff the first ``_BINARY_SNIFF_BYTES`` of ``data`` contain a NUL byte
    (the heuristic git / grep use to classify a file as binary)."""
    return b"\x00" in data[:_BINARY_SNIFF_BYTES]
