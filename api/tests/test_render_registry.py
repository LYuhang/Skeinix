"""Render registry and real @tool_output decorator defaults and overrides."""
import asyncio
import json

from vibecanvas_api.agents.tools.decorator import tool_output
from vibecanvas_api.agents.tools.render import (
    register_render, Rendered, with_fields, field_block, FIELDS_SEPARATOR,
)


# ── default path: tool registers NOTHING, returns its raw payload ──
@tool_output(content_type="application/json", tool="echo_default")
async def echo_default(x):
    return {"echoed": x}


def test_default_path():
    content, art = asyncio.run(echo_default("hi"))
    assert art["status"] == "success"
    assert json.loads(content) == {"echoed": "hi"}      # agent reads the body
    assert art["content_abstract"]             # generic content_type abstract
    assert art["meta"]["content_type"] == "application/json"
    assert art["meta"]["tool"] == "echo_default"


# ── override path: a get_workflow-style render co-located + registered ──
@register_render("wf_demo")
def _wf_render(raw, ctx):
    nodes = raw.get("nodes", [])
    fields = [field_block(f"nodes/{i}/process_fn", "text/python", n["process_fn"])
              for i, n in enumerate(nodes) if "process_fn" in n]
    structural = "{ structural json with [[field:...]] placeholders }"
    return Rendered(content=with_fields(structural, fields), content_type="workflow/json",
                    abstract=f"workflow: {len(nodes)} nodes", extras={"version": "v7"})


@tool_output(content_type="application/json", tool="wf_demo")
async def wf_demo():
    return {"nodes": [{"id": "n0", "process_fn": "def f(row):\n    return row"}]}


def test_override_path_layout_and_abstract():
    content, art = asyncio.run(wf_demo())
    assert art["content_abstract"] == "workflow: 1 nodes"
    assert "nodes/0/process_fn" in content                     # field_path header IN content
    assert "def f(row)" in content                             # clean multi-line text
    assert FIELDS_SEPARATOR in content                         # standard separator
    assert art["meta"]["content_type"] == "workflow/json"
    assert art["artifact"]["handles"]["version"] == "v7"


# ── error path: tool raises → status:error envelope (§4.6) ──
@tool_output(content_type="text/plain", tool="boom")
async def boom():
    e = ValueError("nope")
    e.info = {"hint": "fix"}
    raise e


def test_error_path():
    content, art = asyncio.run(boom())
    assert art["status"] == "error"
    assert art["error"]["code"] == "nope"
    assert content == "nope"                          # agent reads the message (here = code)
    assert art["error"]["info"] == {"hint": "fix"}
