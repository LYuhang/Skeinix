"""Task A2 — envelope v2 form-ladder builder + content_type scheme registry."""
import json

from vibecanvas_api.agents.tools.envelope import (
    make_envelope, abbreviate, abstract_of, as_ref,
    register_abstract_scheme, ENVELOPE_KEY_ORDER,
)


def test_make_envelope_key_order_and_ascii():
    env = make_envelope(
        status="success", error=None, content="héllo",
        auxiliary=[], output_meta={"content_type": "text/plain", "path": None, "tool": "x"},
    )
    s = json.dumps(env, ensure_ascii=False)
    # fixed key order (byte-stability for KV-cache)
    assert list(env.keys()) == ENVELOPE_KEY_ORDER
    # non-ascii preserved (ensure_ascii=False discipline)
    assert "héllo" in s


def test_abbreviate_text_keeps_head_and_tail():
    body = "\n".join(f"line{i}" for i in range(1000))
    out = abbreviate(body, "text/shell", head_tokens=20, tail_tokens=10, path="/exec/x.txt")
    assert "line0" in out          # head
    assert "line999" in out        # tail
    assert "elided" in out         # notice
    assert "/exec/x.txt" in out    # ref to full


def test_abbreviate_json_keeps_top_level_keys():
    body = json.dumps({"a": list(range(500)), "b": {"deep": 1}, "c": 3})
    out = abbreviate(body, "application/json", head_tokens=20, tail_tokens=10, path="/d/x.json")
    assert "a" in out and "b" in out and "c" in out


def test_abstract_of_shell_folds_exit_code():
    a = abstract_of("out\n" * 100, "text/shell", extras={"exit_code": 0}, tool="run_command")
    assert "exit 0" in a or "exit_code" in a


def test_tool_name_override_wins():
    register_abstract_scheme("frobnicate", lambda content, extras: "FROB-SPECIAL")
    a = abstract_of("anything", "text/plain", extras={}, tool="frobnicate")
    assert a == "FROB-SPECIAL"


def test_as_ref_is_a_pointer_string():
    assert as_ref("wrote 2000 lines → /path") == "wrote 2000 lines → /path"
