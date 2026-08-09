from __future__ import annotations

import copy

from vibecanvas_engine.nodes.http_request import HTTPRequestNode


def _http_node() -> dict:
    return {
        "node_id": "node_2",
        "node_name": "create_record",
        "node_type": "HTTPRequestNode",
        "node_description": "Create a record via API",
        "input_fields": {
            "name": {"type": "string", "value": "", "reference": "__start__.name"},
            "api_key": {"type": "string", "value": "", "reference": "__start__.api_key"},
        },
        "output_fields": {
            "response_body": {"type": "object", "description": "API response body"},
            "status_code": {"type": "integer", "description": "HTTP status code"},
            "response_headers": {"type": "object", "description": "Response headers"},
        },
        "node_config": {
            "method": "POST",
            "url": "https://api.example.com/records",
            "headers": {"Content-Type": "application/json"},
            "body": {"format": "json", "content": {"name": "{{name}}"}},
            "auth": {"type": "api_key", "key": "{{api_key}}"},
        },
        "children": [],
        "__attributes__": {"x": 200, "y": 0},
    }


def test_http_request_node_check_accepts_valid_config():
    result = HTTPRequestNode.check(_http_node())
    assert result["status"] == "success"


def test_http_request_node_check_rejects_get_body():
    node = copy.deepcopy(_http_node())
    node["node_config"]["method"] = "GET"
    result = HTTPRequestNode.check(node)
    assert result["status"] == "error"
    assert "body is only allowed for POST/PUT" in result["error_message"]


def test_http_request_node_check_rejects_missing_bearer_token():
    node = copy.deepcopy(_http_node())
    node["node_config"]["auth"] = {"type": "bearer"}
    result = HTTPRequestNode.check(node)
    assert result["status"] == "error"
    assert "auth.token is required" in result["error_message"]


def test_http_request_node_check_rejects_missing_basic_credentials():
    node = copy.deepcopy(_http_node())
    node["node_config"]["auth"] = {"type": "basic", "username": "alice"}
    result = HTTPRequestNode.check(node)
    assert result["status"] == "error"
    assert "auth.username and auth.password are required" in result["error_message"]


def test_http_request_node_check_rejects_output_field_drift():
    node = copy.deepcopy(_http_node())
    node["output_fields"]["extra"] = {"type": "string", "description": "unexpected"}
    result = HTTPRequestNode.check(node)
    assert result["status"] == "error"
    assert "output_fields must be exactly" in result["error_message"]


def test_http_request_node_check_rejects_output_field_type_mismatch():
    node = copy.deepcopy(_http_node())
    node["output_fields"]["status_code"]["type"] = "string"
    result = HTTPRequestNode.check(node)
    assert result["status"] == "error"
    assert "status_code type must be 'integer'" in result["error_message"]
