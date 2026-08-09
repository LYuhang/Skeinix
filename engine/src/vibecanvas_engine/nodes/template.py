# -*- coding: utf-8 -*-
"""TemplateNode — render data into formatted text using Jinja2 templates."""

from copy import deepcopy

import jsonschema

from ..utils import safe_call_with_args
from ..register import node_registry
from .base import BaseNode


_JINJA_ENVS = {}


def _jinja_env(autoescape: bool):
    """Return a cached SandboxedEnvironment, building it on first use.

    lazy: keep jinja2 out of cold-import; the sandboxed environments are only
    built when a TemplateNode actually renders (task #483). Cached per
    autoescape flag so each env is constructed at most once.
    """
    if autoescape not in _JINJA_ENVS:
        from jinja2.sandbox import SandboxedEnvironment
        _JINJA_ENVS[autoescape] = SandboxedEnvironment(autoescape=autoescape)
    return _JINJA_ENVS[autoescape]


# NOTE: media paths are NOT rewritten here. The template author writes raw VFS
# paths (``/run/…``, ``/mount/…``, ``/data/…``) or http(s) URLs directly; the
# FRONTEND renderer signs the local VFS paths (``POST /vfs/sign``) at display
# time. (The old Gradio ``/gradio_api/file=<path>`` rewrite was removed — this
# is not a Gradio app, so that route 404'd and the frontend only had to strip
# it back off.)


@node_registry.register()
class TemplateNode(BaseNode):
    """Render input data into formatted text (HTML/Markdown/text) using Jinja2 templates.

    Media references must be emitted as explicit tags (plain ``{{ url }}`` only
    prints the string); the agent-facing tag patterns live in ``AGENT_SPEC``
    (single source of truth), and the raw-path / frontend-signing engine
    behavior is explained in the module NOTE above.
    """

    CONFIG_SCHEMA = {
        "type": "object",
        "required": ["template", "output_format"],
        "properties": {
            "template": {
                "type": "string",
                "description": "Jinja2 template string. Input field values are available as template variables."
            },
            "output_format": {
                "type": "string",
                "enum": ["html", "markdown", "text"],
                "description": "Output format of the rendered template."
            }
        },
        "additionalProperties": False
    }

    AGENT_SPEC = {
        "summary": "Render input data into HTML, Markdown, or plain text with a sandboxed Jinja2 template.",
        "when_to_use": "Use for deterministic reports, notifications, HTML snippets, Markdown documents, or user-facing views from structured data.",
        "when_not_to_use": "For LLM text generation use PromptNode. For data transformation use TransformNode or CodeNode.",
        "constraints": [
            "Use node_type='TemplateNode' and at most one child.",
            "All input_fields are available as Jinja2 variables, e.g. {{ field_name }}.",
            "Only declared input_fields and standard sandboxed Jinja constructs are available. Do not call implicit helpers such as now(), datetime(), environment variables, or application globals; pass every dynamic value through an input field.",
            "Jinja2 runs in SandboxedEnvironment: no file I/O, subprocess, os access, or imports.",
            "output_fields must be exactly rendered (string) and format (string). format echoes node_config.output_format.",
            "For media paths or URLs, emit explicit HTML/Markdown media tags; plain {{ value }} only prints the string."
        ],
        "config_guide": {
            "template": (
                "Jinja2 template string. Use {{ field_name }} for variables and "
                "{% for %}/{% if %} for loops/branches. For media use tags such as "
                "<img src=\"{{ image }}\">, <video src=\"{{ video }}\" controls></video>, "
                "<audio src=\"{{ audio }}\" controls></audio>, or Markdown image syntax "
                "![alt]({{ image }})."
            ),
            "output_format": "Output format: 'html', 'markdown', or 'text'. This metadata helps downstream consumers decide how to render the output. Use 'html' or 'markdown' when input fields contain media references — 'text' does not render media tags."
        },
        "examples": [
            {
                "scenario": "Render a short HTML report",
                "node_dict": {
                    "node_id": "node_5",
                    "node_name": "render_report",
                    "node_type": "TemplateNode",
                    "node_description": "Render a compact HTML report",
                    "input_fields": {
                        "title": {"type": "string", "value": "", "reference": "summary.title"},
                        "items": {"type": "array", "value": [], "reference": "summary.items"}
                    },
                    "output_fields": {
                        "rendered": {"type": "string", "description": "Rendered content"},
                        "format": {"type": "string", "description": "Output format: html/markdown/text"}
                    },
                    "node_config": {
                        "template": (
                            "<h2>{{ title }}</h2>\n"
                            "<ul>{% for item in items %}<li>{{ item }}</li>{% endfor %}</ul>"
                        ),
                        "output_format": "html"
                    },
                    "children": ["node_6"],
                    "__attributes__": {"x": 400, "y": 0}
                }
            }
        ],
        "display": {
            "name": {"en": "TemplateNode", "zh": "模板渲染节点"},
            "description": {"en": "Render data into formatted text with Jinja2 templates", "zh": "使用 Jinja2 模板将数据渲染为格式化文本"},
            "icon": "template",
            "category": {"en": "Data Processing", "zh": "数据处理"},
        }
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @staticmethod
    @safe_call_with_args(prefix="[TemplateNode Check]: ")
    def check(node_dict: dict) -> bool:
        jsonschema.validate(instance=node_dict, schema=BaseNode.GENERAL_NODE_SCHEMA)

        specific_schema = deepcopy(BaseNode.GENERAL_NODE_SCHEMA)
        specific_schema["properties"]["node_config"] = TemplateNode.CONFIG_SCHEMA
        jsonschema.validate(instance=node_dict, schema=specific_schema)

        jsonschema.validate(instance=node_dict, schema={
            "type": "object",
            "properties": {
                "node_type": {"const": "TemplateNode"},
                "children": {"type": "array", "maxItems": 1}
            }
        })

        output_fields = node_dict.get("output_fields", {})
        assert set(output_fields.keys()) == {"rendered", "format"}, (
            "For TemplateNode, output_fields must be exactly 'rendered' and "
            "'format'."
        )
        assert output_fields["rendered"].get("type") == "string", (
            "For TemplateNode, output_fields.rendered type must be 'string'."
        )
        assert output_fields["format"].get("type") == "string", (
            "For TemplateNode, output_fields.format type must be 'string'."
        )

        # Fail at Check/Commit instead of waiting for a real execution to find
        # an undefined template global.  The sandbox exposes standard Jinja
        # constructs, but application/time/environment helpers are deliberately
        # absent; every dynamic value must enter through a declared input.
        from jinja2 import meta
        env = _jinja_env(autoescape=False)
        ast = env.parse(node_dict["node_config"]["template"])
        undeclared = meta.find_undeclared_variables(ast)
        unknown = sorted(undeclared - set(node_dict.get("input_fields", {})))
        assert not unknown, (
            "For TemplateNode, template variables/functions must be declared "
            f"in input_fields; unknown names: {', '.join(unknown)}."
        )

    @safe_call_with_args(prefix="[TemplateNode Call]: ")
    def __call__(self, inputs: dict, previous_outputs: dict) -> dict:
        template_str = self.node_config["template"]
        # output_format is required by schema; this fallback only guards a
        # malformed config. Keep it aligned with the editor's new-node default
        # ('html') so a missing value renders+labels the same way both sides.
        output_format = self.node_config.get("output_format", "html")
        # autoescape OFF always: an html template (or a value holding raw HTML)
        # must emit real tags, not HTML-escaped entities. The rendered HTML is
        # shown ONLY inside a sandboxed `<iframe sandbox="">` on the frontend
        # (no script execution) — that iframe is the containment boundary.
        env = _jinja_env(autoescape=False)
        tmpl = env.from_string(template_str)
        rendered = tmpl.render(**inputs)
        # Media paths are left raw (/run|/mount|/data or http URLs); the frontend
        # signs local VFS paths at display time. Carry the format IN the output
        # so the renderer always knows how to render (independent of node_config).
        return {"rendered": rendered, "format": output_format}
