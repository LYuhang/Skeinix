"""Unit tests for the single-source file-format module (services.file_format).

Pure (no DB / no sandbox): the canonical content_type mapping + text/binary
detection that read_file / write_file / grep / fileops / vfs_run_context all share.
"""
import mimetypes

import pytest

from vibecanvas_api.services.file_format import (
    _BINARY_SNIFF_BYTES,
    content_type_for,
    is_binary_bytes,
    is_text_content_type,
)


# --- content_type_for: (1) curated extension map FIRST ----------------------

@pytest.mark.parametrize("path,expected", [
    ("/memory/a.py", "text/python"),
    ("/data/run.sh", "text/shell"),
    ("/data/notes.md", "text/markdown"),
    ("/mount/rows.csv", "table/csv"),
    ("/mount/rows.tsv", "table/tsv"),
    ("/mount/rows.jsonl", "table/jsonl"),
    ("/mount/config.json", "application/json"),
    ("/data/page.html", "text/html"),
    ("/data/page.htm", "text/html"),
    ("/data/log.log", "text/plain"),
    ("/data/conf.yaml", "text/plain"),
    ("/data/pic.png", "image/png"),
    ("/data/pic.jpeg", "image/jpeg"),
    ("/data/clip.mp3", "audio/mpeg"),
    ("/data/clip.mp4", "video/mp4"),
    ("/data/doc.pdf", "application/pdf"),
    ("/data/deck.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ("/data/workbook.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
    ("/data/diagram.drawio", "application/vnd.jgraph.mxfile"),
])
def test_content_type_extension_map(path, expected):
    assert content_type_for(path) == expected


def test_extension_map_beats_mimetypes_for_custom_subtypes():
    """The curated map MUST win over mimetypes — otherwise our compaction/frontend
    subtypes (table/csv, text/python) get clobbered by stdlib guesses (text/csv,
    text/x-python) and miss the compaction policy registry."""
    # mimetypes would say text/csv — we deliberately want table/csv.
    assert mimetypes.guess_type("x.csv")[0] == "text/csv"
    assert content_type_for("x.csv") == "table/csv"
    # mimetypes' python guess (text/x-python or application/...) is NOT text/python.
    assert content_type_for("x.py") == "text/python"
    assert mimetypes.guess_type("x.py")[0] != "text/python"


# --- content_type_for: (2) mimetypes fallback ------------------------------

def test_mimetypes_fallback_for_unlisted_extension():
    """An extension we don't curate but mimetypes knows → mimetypes' value."""
    expected = mimetypes.guess_type("x.rtf")[0]
    assert expected is not None
    assert content_type_for("x.rtf") == expected


# --- content_type_for: (3) NUL-sniff / default fallback --------------------

def test_no_extension_no_data_defaults_text_plain():
    assert content_type_for("/memory/note") == "text/plain"


def test_no_extension_with_binary_data_is_octet_stream():
    assert content_type_for("/memory/blob", b"PK\x03\x04\x00stuff") == "application/octet-stream"


def test_no_extension_with_text_data_is_text_plain():
    assert content_type_for("/memory/note", b"plain text, no NUL") == "text/plain"


# --- is_binary_bytes: NUL sniff within the 8192 window ----------------------

def test_is_binary_bytes_detects_nul():
    assert is_binary_bytes(b"abc\x00def") is True
    assert is_binary_bytes(b"abc def ghi") is False
    assert is_binary_bytes(b"") is False


def test_is_binary_bytes_only_sniffs_the_window():
    """A NUL at/after byte _BINARY_SNIFF_BYTES is outside the sniff window."""
    inside = b"a" * (_BINARY_SNIFF_BYTES - 1) + b"\x00"
    outside = b"a" * _BINARY_SNIFF_BYTES + b"\x00"
    assert is_binary_bytes(inside) is True
    assert is_binary_bytes(outside) is False


# --- is_text_content_type ---------------------------------------------------

@pytest.mark.parametrize("ct", [
    "text/plain", "text/python", "text/markdown", "application/json",
    "table/csv", "table/jsonl", "json", "text",
])
def test_is_text_content_type_true(ct):
    assert is_text_content_type(ct) is True


@pytest.mark.parametrize("ct", [
    "image/png", "application/octet-stream", "application/pdf", "audio/mpeg", "", None,
])
def test_is_text_content_type_false(ct):
    assert is_text_content_type(ct) is False
