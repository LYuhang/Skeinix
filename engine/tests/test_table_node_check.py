from __future__ import annotations

import copy

from vibecanvas_engine.nodes.table_read import TableReadNode
from vibecanvas_engine.nodes.table_write import TableWriteNode


def _table_read_node() -> dict:
    return {
        "node_id": "node_2",
        "node_name": "read_data",
        "node_type": "TableReadNode",
        "node_description": "Read tabular data",
        "input_fields": {},
        "output_fields": {
            "rows": {"type": "array", "description": "Rows"},
            "headers": {"type": "array", "description": "Column names"},
            "row_count": {"type": "integer", "description": "Number of rows"},
            "schema": {"type": "object", "description": "Inferred row schema"},
        },
        "node_config": {
            "file_path": "/mount/input.jsonl",
            "file_format": "auto",
            "offset": 0,
            "limit": 100,
        },
        "children": [],
        "__attributes__": {"x": 200, "y": 0},
    }


def _table_write_node() -> dict:
    return {
        "node_id": "node_3",
        "node_name": "write_data",
        "node_type": "TableWriteNode",
        "node_description": "Write tabular data",
        "input_fields": {
            "rows": {"type": "array", "value": [], "reference": "processor.rows"},
        },
        "output_fields": {
            "file_path": {"type": "string", "description": "Written file path"},
            "rows_written": {"type": "integer", "description": "Rows written"},
        },
        "node_config": {
            "file_path": "/run/output.jsonl",
            "file_format": "auto",
            "write_mode": "overwrite",
            "data_write": "rows",
        },
        "children": [],
        "__attributes__": {"x": 400, "y": 0},
    }


def test_table_read_check_accepts_required_outputs():
    result = TableReadNode.check(_table_read_node())
    assert result["status"] == "success"


def test_table_read_check_rejects_output_field_drift():
    node = copy.deepcopy(_table_read_node())
    node["output_fields"]["extra"] = {"type": "string", "description": "unexpected"}
    result = TableReadNode.check(node)
    assert result["status"] == "error"
    assert "output_fields must be exactly" in result["error_message"]


def test_table_read_check_rejects_agent_workspace_path():
    node = copy.deepcopy(_table_read_node())
    node["node_config"]["file_path"] = "/data/input.jsonl"
    result = TableReadNode.check(node)
    assert result["status"] == "error"


def test_table_read_check_rejects_relative_path():
    node = copy.deepcopy(_table_read_node())
    node["node_config"]["file_path"] = "input.jsonl"
    result = TableReadNode.check(node)
    assert result["status"] == "error"


def test_table_read_check_rejects_output_type_mismatch():
    node = copy.deepcopy(_table_read_node())
    node["output_fields"]["row_count"]["type"] = "number"
    result = TableReadNode.check(node)
    assert result["status"] == "error"
    assert "row_count type must be 'integer'" in result["error_message"]


def test_table_write_check_accepts_required_outputs():
    result = TableWriteNode.check(_table_write_node())
    assert result["status"] == "success"


def test_table_write_check_rejects_output_field_drift():
    node = copy.deepcopy(_table_write_node())
    del node["output_fields"]["rows_written"]
    result = TableWriteNode.check(node)
    assert result["status"] == "error"
    assert "output_fields must be exactly" in result["error_message"]


def test_table_write_check_rejects_agent_workspace_path():
    node = copy.deepcopy(_table_write_node())
    node["node_config"]["file_path"] = "/data/output.jsonl"
    result = TableWriteNode.check(node)
    assert result["status"] == "error"


def test_table_write_check_rejects_relative_path():
    node = copy.deepcopy(_table_write_node())
    node["node_config"]["file_path"] = "output.jsonl"
    result = TableWriteNode.check(node)
    assert result["status"] == "error"


def test_table_write_check_rejects_output_type_mismatch():
    node = copy.deepcopy(_table_write_node())
    node["output_fields"]["file_path"]["type"] = "object"
    result = TableWriteNode.check(node)
    assert result["status"] == "error"
    assert "file_path type must be 'string'" in result["error_message"]


def test_table_write_check_rejects_unknown_data_write_field():
    node = copy.deepcopy(_table_write_node())
    node["node_config"]["data_write"] = "missing_rows"
    result = TableWriteNode.check(node)
    assert result["status"] == "error"
    assert "data_write field 'missing_rows' must exist" in result["error_message"]
