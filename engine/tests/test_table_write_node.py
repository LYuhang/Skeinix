# -*- coding: utf-8 -*-
"""TableWriteNode — explicit `data_write` source + write boundaries."""


from vibecanvas_engine.nodes.table_write import TableWriteNode
from vibecanvas_engine.nodes import table_io


def _node(config: dict, output_fields=None) -> TableWriteNode:
    return TableWriteNode(
        node_id="node_1",
        node_name="writer",
        node_type="TableWriteNode",
        node_description="",
        input_fields={},
        output_fields=output_fields or {
            "file_path": {"type": "string", "description": ""},
            "rows_written": {"type": "integer", "description": ""},
        },
        node_config=config,
        children=[],
    )


def test_data_write_object_writes_one_row(tmp_path):
    path = str(tmp_path / "out.csv")
    node = _node({"file_path": path, "write_mode": "overwrite", "data_write": "rec"})
    res = node({"rec": {"a": 1, "b": 2}}, {}, extra={})
    assert res["status"] == "success", res
    assert res["output"]["rows_written"] == 1
    assert table_io.read_rows(path, "csv") == [{"a": "1", "b": "2"}]


def test_data_write_list_writes_n_rows_schema_from_first(tmp_path):
    path = str(tmp_path / "out.jsonl")
    node = _node({"file_path": path, "write_mode": "overwrite", "data_write": "items"})
    res = node({"items": [{"x": 1}, {"x": 2}, {"x": 3}]}, {}, extra={})
    assert res["status"] == "success", res
    assert res["output"]["rows_written"] == 3
    assert table_io.read_rows(path, "jsonl") == [{"x": 1}, {"x": 2}, {"x": 3}]


def test_data_write_missing_field_raises(tmp_path):
    path = str(tmp_path / "out.csv")
    node = _node({"file_path": path, "write_mode": "overwrite", "data_write": "nope"})
    res = node({"rec": {"a": 1}}, {}, extra={})
    assert res["status"] == "error"
    assert "not among" in res["error_message"]


def test_data_write_unsupported_type_raises(tmp_path):
    path = str(tmp_path / "out.csv")
    node = _node({"file_path": path, "write_mode": "overwrite", "data_write": "s"})
    res = node({"s": "just a string"}, {}, extra={})
    assert res["status"] == "error"
    assert "must be an object or a list" in res["error_message"]


def test_data_write_list_with_non_dict_item_raises(tmp_path):
    path = str(tmp_path / "out.csv")
    node = _node({"file_path": path, "write_mode": "overwrite", "data_write": "items"})
    res = node({"items": [{"x": 1}, 5]}, {}, extra={})
    assert res["status"] == "error"
    assert "not every item is an object" in res["error_message"]


def test_append_schema_mismatch_raises(tmp_path):
    path = str(tmp_path / "out.csv")
    _node({"file_path": path, "write_mode": "overwrite", "data_write": "r"})(
        {"r": {"a": 1, "b": 2}}, {}, extra={}
    )
    # Append data with DIFFERENT columns → mismatch error.
    res = _node({"file_path": path, "write_mode": "append", "data_write": "r"})(
        {"r": {"a": 9, "c": 3}}, {}, extra={}
    )
    assert res["status"] == "error"
    assert "schema mismatch" in res["error_message"]


def test_append_matching_schema_appends(tmp_path):
    path = str(tmp_path / "out.csv")
    _node({"file_path": path, "write_mode": "overwrite", "data_write": "r"})(
        {"r": [{"a": 1, "b": 2}]}, {}, extra={}
    )
    res = _node({"file_path": path, "write_mode": "append", "data_write": "r"})(
        {"r": [{"a": 3, "b": 4}]}, {}, extra={}
    )
    assert res["status"] == "success", res
    assert table_io.read_rows(path, "csv") == [
        {"a": "1", "b": "2"},
        {"a": "3", "b": "4"},
    ]


def test_back_compat_auto_detect_without_data_write(tmp_path):
    path = str(tmp_path / "out.jsonl")
    node = _node({"file_path": path, "write_mode": "overwrite"})
    res = node({"rows": [{"k": "v"}], "other": "x"}, {}, extra={})
    assert res["status"] == "success", res
    assert table_io.read_rows(path, "jsonl") == [{"k": "v"}]
