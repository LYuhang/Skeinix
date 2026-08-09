"""Tests for workflow execution's internal tabular file backend."""
import io
import types

import pytest

from vibecanvas_api.services.platform_mcp.run_tools import table_io as _table
from vibecanvas_api.agents.tools.decorator import ToolError

CSV = "name,region,age\nAlice,North,30\nBob,South,25\nCarol,North,41\n"
_LEAK = "gvisor pool exhausted: fd 7 broken pipe at /opt/sandbox/internal"


def _rt():
    """A runtime with a context that carries no vfs (offload becomes a no-op)."""
    return types.SimpleNamespace(context=types.SimpleNamespace())


# ---------------------------------------------------------------------------
# session stubs
# ---------------------------------------------------------------------------

def _text_session(content=CSV, *, error=None, kind="text", raise_runtime=False, sink=None):
    """A session whose text read/write returns canned fileop dicts (or raises an
    INTERNAL RuntimeError to prove it is never leaked)."""
    async def _rf(path):
        if raise_runtime:
            raise RuntimeError(_LEAK)
        if error:
            return {"ok": False, "error": error}
        return {"ok": True, "kind": kind, "content": content}

    async def _wf(path, text):
        if raise_runtime:
            raise RuntimeError(_LEAK)
        if error:
            return {"ok": False, "error": error}
        if sink is not None:
            sink["text"], sink["path"] = text, path
        return {"ok": True, "bytes": len(text.encode())}

    return types.SimpleNamespace(read_file=_rf, write_file=_wf)


def _make_xlsx(sheets: dict) -> bytes:
    """Build xlsx bytes from {sheet_name: [row dicts]} — multi-sheet capable."""
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        cols = list(rows[0].keys()) if rows else []
        ws.append(cols)
        for r in rows:
            ws.append([r.get(c) for c in cols])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _xlsx_session(sheets=None, *, raise_runtime=False, missing=False):
    """A session backing xlsx via read_bytes/write_bytes over a MUTABLE byte store —
    so read-modify-write (sheet preservation) is observable across calls. ``sheets``
    seeds the workbook ({name: rows}); ``missing`` makes read_bytes report not_found."""
    store = {"data": None if (missing or sheets is None) else _make_xlsx(sheets)}

    async def _rb(path):
        if raise_runtime:
            raise RuntimeError(_LEAK)
        if store["data"] is None:
            return {"ok": False, "error": "not_found"}
        return {"ok": True, "data": store["data"]}

    async def _wb(path, payload):
        if raise_runtime:
            raise RuntimeError(_LEAK)
        store["data"] = payload
        return {"ok": True, "bytes": len(payload)}

    return types.SimpleNamespace(read_bytes=_rb, write_bytes=_wb)


# ---------------------------------------------------------------------------
# _read_rows — backend read (text + xlsx + every error mapping)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_rows_parses_text_table():
    rows, cols = await _table._read_rows("/data/people.csv", _text_session())
    assert [r["name"] for r in rows] == ["Alice", "Bob", "Carol"]
    assert cols == ["name", "region", "age"]


@pytest.mark.asyncio
async def test_read_rows_empty_csv_keeps_header_columns():
    # header-only CSV → zero rows but the column names are preserved (not lost).
    rows, cols = await _table._read_rows("/data/empty.csv", _text_session(content="name,region\n"))
    assert rows == []
    assert cols == ["name", "region"]


@pytest.mark.asyncio
async def test_read_rows_missing_is_path_not_found():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/nope.csv", _text_session(error="not_found"))
    assert str(ei.value) == "path_not_found"
    assert ei.value.message == "path '/data/nope.csv' does not exist"


@pytest.mark.asyncio
async def test_read_rows_outside_roots_is_invalid_path():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/../etc/x.csv",
                                _text_session(error="path_outside_roots"))
    assert str(ei.value) == "invalid_path"
    assert "outside" in ei.value.message


@pytest.mark.asyncio
async def test_read_rows_generic_backend_error_is_read_failed():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/x.csv", _text_session(error="weird_internal_thing"))
    assert str(ei.value) == "read_failed"


@pytest.mark.asyncio
async def test_read_rows_binary_kind_is_not_text():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/x.csv", _text_session(kind="binary"))
    assert str(ei.value) == "not_text"


@pytest.mark.asyncio
async def test_read_rows_unknown_extension_is_not_tabular():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/notes.txt", _text_session(content="hello"))
    assert str(ei.value) == "not_tabular"


@pytest.mark.asyncio
async def test_read_rows_runtime_error_scrubbed_to_no_workspace():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/x.csv", _text_session(raise_runtime=True))
    assert str(ei.value) == "no_workspace"
    assert "gvisor" not in (ei.value.message or "")     # raw backend detail not leaked
    assert "workspace" in ei.value.message


@pytest.mark.asyncio
async def test_read_rows_xlsx_single_sheet_auto():
    sess = _xlsx_session({"Sheet1": [{"name": "Alice", "region": "North"},
                                     {"name": "Bob", "region": "South"}]})
    rows, _ = await _table._read_rows("/data/people.xlsx", sess)
    assert [r["name"] for r in rows] == ["Alice", "Bob"]


@pytest.mark.asyncio
async def test_read_rows_xlsx_multi_sheet_requires_sheet():
    sess = _xlsx_session({"Summary": [{"a": "1"}], "Q1": [{"a": "2"}]})
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/book.xlsx", sess)
    assert str(ei.value) == "sheet_required"
    assert "Summary" in ei.value.message and "Q1" in ei.value.message


@pytest.mark.asyncio
async def test_read_rows_xlsx_named_sheet():
    sess = _xlsx_session({"Summary": [{"a": "1"}],
                          "Q1": [{"region": "North"}, {"region": "South"}]})
    rows, _ = await _table._read_rows("/data/book.xlsx", sess, "Q1")
    assert [r["region"] for r in rows] == ["North", "South"]


@pytest.mark.asyncio
async def test_read_rows_xlsx_unknown_sheet():
    sess = _xlsx_session({"Summary": [{"a": "1"}], "Q1": [{"a": "2"}]})
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/book.xlsx", sess, "Q9")
    assert str(ei.value) == "sheet_not_found"
    assert "Q9" in ei.value.message and "Q1" in ei.value.message


@pytest.mark.asyncio
async def test_read_rows_xlsx_runtime_error_scrubbed():
    with pytest.raises(ToolError) as ei:
        await _table._read_rows("/data/x.xlsx", _xlsx_session(raise_runtime=True))
    assert str(ei.value) == "no_workspace"
    assert "gvisor" not in (ei.value.message or "")


# ---------------------------------------------------------------------------
# _write_rows — backend write (text + xlsx + every error mapping)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_write_rows_text_writes_header_and_rows():
    sink = {}
    n = await _table._write_rows("/data/out.csv", [{"a": "1", "b": "2"}],
                                 _text_session(sink=sink))
    assert n > 0
    assert sink["text"].splitlines()[0] == "a,b"        # header row written
    assert "1,2" in sink["text"]


@pytest.mark.asyncio
async def test_write_rows_outside_roots_is_invalid_path():
    with pytest.raises(ToolError) as ei:
        await _table._write_rows("/data/../x.csv", [{"a": "1"}],
                                 _text_session(error="path_outside_roots"))
    assert str(ei.value) == "invalid_path"


@pytest.mark.asyncio
async def test_write_rows_generic_backend_error_is_write_failed():
    with pytest.raises(ToolError) as ei:
        await _table._write_rows("/data/x.csv", [{"a": "1"}],
                                 _text_session(error="OSError Errno 28 no space /internal/blobs"))
    assert str(ei.value) == "write_failed"
    assert "Errno" not in (ei.value.message or "")       # internal detail not leaked


@pytest.mark.asyncio
async def test_write_rows_unsupported_format_is_bad_format():
    with pytest.raises(ToolError) as ei:
        await _table._write_rows("/data/x.parquet", [{"a": "1"}], _text_session())
    assert str(ei.value) == "bad_format"


@pytest.mark.asyncio
async def test_write_rows_runtime_error_scrubbed_to_no_workspace():
    with pytest.raises(ToolError) as ei:
        await _table._write_rows("/data/x.csv", [{"a": "1"}],
                                 _text_session(raise_runtime=True))
    assert str(ei.value) == "no_workspace"
    assert "gvisor" not in (ei.value.message or "")


@pytest.mark.asyncio
async def test_write_rows_xlsx_new_file_single_sheet():
    sess = _xlsx_session(missing=True)                       # file does not exist yet
    n = await _table._write_rows("/data/new.xlsx", [{"name": "Alice"}], sess)
    assert n > 0
    rows, _ = await _table._read_rows("/data/new.xlsx", sess)   # single sheet → auto-selected
    assert rows == [{"name": "Alice"}]


@pytest.mark.asyncio
async def test_write_rows_xlsx_preserves_other_sheets():
    sess = _xlsx_session({"Summary": [{"metric": "users", "val": "100"}],
                          "Q1": [{"region": "North", "sales": "50"}]})
    # overwrite Q1; Summary must survive the read-modify-write
    n = await _table._write_rows("/data/book.xlsx",
                                 [{"region": "South", "sales": "80"}], sess, "Q1")
    assert n > 0
    q1, _ = await _table._read_rows("/data/book.xlsx", sess, "Q1")
    summ, _ = await _table._read_rows("/data/book.xlsx", sess, "Summary")
    assert q1 == [{"region": "South", "sales": "80"}]
    assert summ == [{"metric": "users", "val": "100"}]       # preserved, not clobbered


@pytest.mark.asyncio
async def test_write_rows_xlsx_multi_sheet_requires_sheet():
    sess = _xlsx_session({"A": [{"x": "1"}], "B": [{"x": "2"}]})
    with pytest.raises(ToolError) as ei:
        await _table._write_rows("/data/book.xlsx", [{"x": "9"}], sess)   # no sheet given
    assert str(ei.value) == "sheet_required"


@pytest.mark.asyncio
async def test_write_rows_xlsx_new_sheet_in_existing_book():
    sess = _xlsx_session({"Summary": [{"metric": "users", "val": "100"}]})
    # a named sheet that does not exist yet → created, Summary preserved
    await _table._write_rows("/data/book.xlsx", [{"k": "v"}], sess, "Extra")
    extra, _ = await _table._read_rows("/data/book.xlsx", sess, "Extra")
    summ, _ = await _table._read_rows("/data/book.xlsx", sess, "Summary")
    assert extra == [{"k": "v"}] and summ == [{"metric": "users", "val": "100"}]
