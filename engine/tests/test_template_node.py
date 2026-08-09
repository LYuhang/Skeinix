# -*- coding: utf-8 -*-
"""TemplateNode — Jinja2 rendering + the {rendered, format} output contract.

Covers the autoescape-OFF behaviour (raw HTML / markdown emitted, NOT escaped
entities), the `format` field carried in the output, and the 2-field `check`.
"""

from vibecanvas_engine.nodes.template import TemplateNode


def _node(template, output_format, output_fields=None, input_fields=None):
    if output_fields is None:
        output_fields = {
            "rendered": {"type": "string", "description": ""},
            "format": {"type": "string", "description": ""},
        }
    return TemplateNode(
        node_id="node_1",
        node_name="tmpl",
        node_type="TemplateNode",
        node_description="",
        input_fields=input_fields or {},
        output_fields=output_fields,
        node_config={"template": template, "output_format": output_format},
        children=[],
    )


def test_html_output_is_not_escaped_and_carries_format():
    node = _node("{{ html }}", "html")
    res = node({"html": '<img src="x">'}, {})
    out = res["output"]
    assert '<img src="x">' in out["rendered"]
    assert "&lt;" not in out["rendered"]
    assert out["format"] == "html"


def test_markdown_output_unescaped_and_carries_format():
    node = _node("## Title", "markdown")
    out = node({}, {})["output"]
    assert out["rendered"] == "## Title"
    assert out["format"] == "markdown"


def test_check_requires_both_rendered_and_format():
    good = {
        "node_id": "node_1", "node_name": "tmpl", "node_type": "TemplateNode",
        "node_description": "",
        "input_fields": {
            "x": {"type": "string", "value": "", "reference": ""},
        },
        "children": [],
        "output_fields": {
            "rendered": {"type": "string", "description": ""},
            "format": {"type": "string", "description": ""},
        },
        "node_config": {"template": "{{ x }}", "output_format": "text"},
    }
    res = TemplateNode.check(good)
    assert res["status"] == "success", res

    bad = {
        **good,
        "output_fields": {"rendered": {"type": "string", "description": ""}},
    }
    res_bad = TemplateNode.check(bad)
    assert res_bad["status"] == "error"
    assert "format" in res_bad["error_message"]


def test_check_rejects_output_type_mismatch():
    bad = {
        "node_id": "node_1", "node_name": "tmpl", "node_type": "TemplateNode",
        "node_description": "", "input_fields": {}, "children": [],
        "output_fields": {
            "rendered": {"type": "object", "description": ""},
            "format": {"type": "string", "description": ""},
        },
        "node_config": {"template": "{{ x }}", "output_format": "text"},
    }
    res = TemplateNode.check(bad)
    assert res["status"] == "error"
    assert "rendered type must be 'string'" in res["error_message"]


def test_check_rejects_undeclared_template_global():
    node = {
        "node_id": "node_1", "node_name": "tmpl", "node_type": "TemplateNode",
        "node_description": "", "input_fields": {}, "children": [],
        "output_fields": {
            "rendered": {"type": "string", "description": ""},
            "format": {"type": "string", "description": ""},
        },
        "node_config": {
            "template": "Generated at {{ now() }}",
            "output_format": "text",
        },
    }
    result = TemplateNode.check(node)
    assert result["status"] == "error"
    assert "unknown names: now" in result["error_message"]


def test_check_accepts_declared_template_inputs_and_standard_loop():
    node = {
        "node_id": "node_1", "node_name": "tmpl", "node_type": "TemplateNode",
        "node_description": "",
        "input_fields": {
            "title": {"type": "string", "value": "", "reference": ""},
            "items": {"type": "array", "value": [], "reference": ""},
        },
        "children": [],
        "output_fields": {
            "rendered": {"type": "string", "description": ""},
            "format": {"type": "string", "description": ""},
        },
        "node_config": {
            "template": "{{ title }}{% for item in items %}{{ item }}{% endfor %}",
            "output_format": "text",
        },
    }
    assert TemplateNode.check(node)["status"] == "success"
