"""RFC 6901 JSON Pointer — parse + resolve. Pure-function unit tests.

Ported from legacy vibecanvas/demo/tests/test_json_pointer.py, trimmed to the
two functions next-repo ports (parse + resolve; no from_expr / is_* helpers).
"""
import pytest

from vibecanvas_api.utils import json_pointer


def test_parse_empty_is_whole_doc():
    assert json_pointer.parse("") == []


def test_parse_decodes_escapes():
    assert json_pointer.parse("/a~1b/c~0d") == ["a/b", "c~d"]


def test_parse_rejects_missing_leading_slash():
    with pytest.raises(ValueError):
        json_pointer.parse("node_1/children")


def test_resolve_dict_key():
    doc = {"node_1": {"node_config": {"temperature": 0.7}}}
    parent, key, exists = json_pointer.resolve(doc, "/node_1/node_config/temperature")
    assert parent == {"temperature": 0.7} and key == "temperature" and exists is True


def test_resolve_missing_dict_key_reports_not_exists():
    doc = {"node_1": {"node_config": {}}}
    parent, key, exists = json_pointer.resolve(doc, "/node_1/node_config/temperature")
    assert key == "temperature" and exists is False


def test_resolve_list_index():
    doc = {"node_1": {"children": ["node_2", "node_3"]}}
    parent, key, exists = json_pointer.resolve(doc, "/node_1/children/1")
    assert parent == ["node_2", "node_3"] and key == 1 and exists is True


def test_resolve_list_append_sentinel():
    doc = {"node_1": {"children": ["node_2"]}}
    parent, key, exists = json_pointer.resolve(doc, "/node_1/children/-")
    assert key == "-" and exists is False


def test_resolve_list_index_out_of_range_not_exists():
    doc = {"node_1": {"children": ["node_2"]}}
    _parent, key, exists = json_pointer.resolve(doc, "/node_1/children/5")
    assert key == 5 and exists is False


def test_resolve_bad_list_index_raises():
    doc = {"node_1": {"children": ["node_2"]}}
    with pytest.raises(ValueError):
        json_pointer.resolve(doc, "/node_1/children/notanint")


def test_resolve_through_scalar_raises():
    doc = {"node_1": {"x": 5}}
    with pytest.raises((TypeError, KeyError)):
        json_pointer.resolve(doc, "/node_1/x/deeper")
