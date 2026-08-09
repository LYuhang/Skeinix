"""Envelope helpers and content-type taxonomy."""
import json

from vibecanvas_api.agents.tools._envelope import (
    tool_ok, tool_err, CAP_ROWS, INLINE_CHARS, fill_output_data,
)
from vibecanvas_api.storage.vfs_store import _EXT


def test_tool_ok_shape_and_fixed_key_order():
    s = tool_ok("did a thing", {"path": "/data/x.jsonl", "content_type": "table/jsonl"})
    assert list(json.loads(s).keys()) == ["status", "error", "abstract", "output"]
    p = json.loads(s)
    assert p["status"] == "success" and p["error"] is None
    assert p["abstract"] == "did a thing"
    assert p["output"]["path"] == "/data/x.jsonl"


def test_tool_ok_is_byte_deterministic():
    a = tool_ok("s", {"path": "/data/x.jsonl", "content_type": "table/jsonl"})
    b = tool_ok("s", {"path": "/data/x.jsonl", "content_type": "table/jsonl"})
    assert a == b


def test_tool_err_shape():
    s = tool_err("path_not_found", "no such file")
    p = json.loads(s)
    assert list(p.keys()) == ["status", "error", "abstract", "output"]
    assert p["status"] == "error" and p["error"] == "path_not_found"
    assert p["abstract"] == "no such file" and p["output"] is None


def test_ext_covers_new_and_legacy_spellings():
    assert _EXT["table/jsonl"] == "jsonl"
    assert _EXT["application/json"] == "json"
    assert _EXT["text/plain"] == "txt"
    assert _EXT["text/python"] == "py"
    assert _EXT["json"] == "json"
    assert _EXT["text"] == "txt"


def test_cap_rows_is_positive_int():
    assert isinstance(CAP_ROWS, int) and CAP_ROWS > 0


# --------------------------------------------------------------------------- #
# fill_output_data — §4.1a: small inlines, large records full_tokens (no blind omit)
# --------------------------------------------------------------------------- #

def test_fill_output_data_small_inlines_unchanged():
    out = {"path": "/data/x.json", "content_type": "application/json"}
    val = {"rows": [1, 2, 3]}
    fill_output_data(out, val)
    assert out["data"] == val
    assert "full_tokens" not in out and "full_chars" not in out


def test_fill_output_data_large_records_full_tokens_not_blind_omit():
    out = {"path": "/data/big.txt", "content_type": "text/plain"}
    big = "y" * (INLINE_CHARS + 5000)
    fill_output_data(out, big)
    # large body is NOT inlined (would bust context) ...
    assert "data" not in out
    # ... but the FULL size is recorded so the middleware can re-hydrate head+tail
    # from VFS by path — NOT a bare data:None with no signal a big body exists.
    assert out["full_chars"] == len(big)
    assert isinstance(out["full_tokens"], int) and out["full_tokens"] > 0
    assert out["full_tokens"] == len(big) // 4   # chars≈4


def test_fill_output_data_at_boundary_inlines():
    out = {"path": "/x", "content_type": "text/plain"}
    s = "z" * INLINE_CHARS  # exactly the cap → still inline
    fill_output_data(out, s)
    assert out["data"] == s and "full_tokens" not in out


def test_fill_output_data_returns_out():
    out = {"path": "/x", "content_type": "text/plain"}
    assert fill_output_data(out, "small") is out
