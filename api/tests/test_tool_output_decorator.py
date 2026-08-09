"""build_content_and_artifact — guardrail + simplified artifact (REVISION 2026-06-25)."""
from vibecanvas_api.config import CompactionV2Config
from vibecanvas_api.agents.tools.decorator import (
    build_content_and_artifact, _error_content_and_artifact, ToolError,
)

CFG = CompactionV2Config({"inline_chars": 100, "offload_preview_chars": 40})


def test_small_content_full_no_path():
    content, art = build_content_and_artifact("hello", "text/plain", abstract="a", cfg=CFG)
    assert content == "hello"                        # small → full body
    assert art["status"] == "success"
    assert art["payload"]["ref"] is None
    assert art["content"] == "hello"
    assert art["content_abstract"] == "a"
    assert art["meta"]["content_type"] == "text/plain"


def test_empty_success_uses_abstract_as_model_visible_feedback():
    content, art = build_content_and_artifact(
        "", "text/shell", abstract="ran `mkdir -p /data/out`, exit 0, 0 lines", cfg=CFG
    )

    assert content == "ran `mkdir -p /data/out`, exit 0, 0 lines"
    assert art["content"] == content
    assert art["content_abstract"] == content
    # Artifact identity and size continue to describe the raw tool output,
    # rather than the synthetic model-facing feedback.
    assert art["payload"]["size"]["chars"] == 0


def test_non_empty_success_output_remains_byte_identical():
    raw = "line one\nline two\n"
    content, art = build_content_and_artifact(
        raw, "text/shell", abstract="ran command, exit 0, 2 lines", cfg=CFG
    )

    assert content == raw
    assert art["content"] == raw


def test_large_non_viewer_guardrail_preview_plus_ref():
    big = "X" * 500                                   # > inline_chars 100
    captured = {}

    def offload(serialized, ct):
        captured["s"] = serialized
        return "/memory/outputs/out_ab12.txt"

    content, art = build_content_and_artifact(
        big, "text/plain", abstract="ran", cfg=CFG, offload=offload)
    assert art["payload"]["ref"] == "/memory/outputs/out_ab12.txt"
    assert art["ref"] == "/memory/outputs/out_ab12.txt"
    assert "truncated" in content                     # the notice
    assert "read_file" in content                     # how to re-read
    assert "/memory/outputs/out_ab12.txt" in content  # the ref
    assert len(content) < len(big)                    # bounded (preview + notice, not the full body)
    assert captured["s"] == big                       # FULL body offloaded
    assert "/memory/outputs/out_ab12.txt" in art["content_abstract"]


def test_viewer_returns_full_even_when_large():
    big = "Y" * 500
    content, art = build_content_and_artifact(
        big, "text/plain", abstract="read", is_viewer=True, cfg=CFG)
    assert content == big                             # read_file viewer → full content
    assert "truncated" not in content


def test_per_tool_inline_override_keeps_larger_non_viewer_output_inline():
    big = "Z" * 500
    content, art = build_content_and_artifact(
        big, "text/plain", abstract="schema", cfg=CFG, inline_chars=1000)
    assert content == big
    assert art["payload"]["kind"] == "inline"
    assert "truncated" not in content


def test_stale_on_reread_and_handles_and_aux():
    aux = [{"id": "a1", "type": "image", "path": "/p.png"}]
    content, art = build_content_and_artifact(
        "y", "text/plain", abstract="a", stale_on_reread=True,
        auxiliary=aux, handles={"node_id": "n3"}, cfg=CFG)
    assert art["meta"]["stale_on_reread"] is True
    assert art["artifact"]["auxiliary"] == aux
    assert art["artifact"]["handles"] == {"node_id": "n3"}


def test_error_capture():
    content, art = _error_content_and_artifact(
        ToolError("unknown_node", "node n3 not found", info={"hint": "fix"}), "node_execute")
    assert content == "node n3 not found"
    assert art["status"] == "error"
    assert art["error"]["code"] == "unknown_node"
    assert art["error"]["info"] == {"hint": "fix"}
    assert art["meta"]["tool"] == "node_execute"
