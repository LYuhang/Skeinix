"""Pure deterministic head/tail/notice renderer without an LLM.

The default in-context FORM for a fresh LARGE tool output: head(N tok) + an
elision notice (with the elided count + the VFS path) + tail(M tok). A tiny body
(≤ head+tail budget) is returned whole.
"""
from vibecanvas_api.agents.middleware.compaction_forms import (
    render_head_tail_notice,
    output_full_tokens,
    parse_envelope,
)
from vibecanvas_api.agents.token_accounting import count_tokens


def _body(n_lines: int) -> str:
    return "\n".join(f"line {i} content here" for i in range(n_lines))


def test_tiny_body_returned_whole():
    body = "small body\nsecond line"
    out = render_head_tail_notice(body, path="/exec/x.log", head_tokens=1500,
                                  tail_tokens=500)
    assert out == body          # under budget → no truncation, no notice


def test_large_body_head_tail_with_notice_and_path():
    body = _body(5000)
    out = render_head_tail_notice(body, path="/exec/big.log", head_tokens=50,
                                  tail_tokens=20, full_tokens=count_tokens(body, ""))
    # head present (start of body), tail present (end of body)
    assert out.startswith("line 0 content here")
    assert out.rstrip().endswith("content here")
    assert "line 4999 content here" in out      # the tail
    # notice carries the elided count + the VFS path + re-read hint
    assert "tokens elided" in out
    assert "/exec/big.log" in out
    assert "read_file" in out


def test_head_and_tail_budgets_respected():
    body = _body(5000)
    head_budget, tail_budget = 40, 15
    out = render_head_tail_notice(body, path="/exec/big.log",
                                  head_tokens=head_budget, tail_tokens=tail_budget,
                                  full_tokens=count_tokens(body, ""))
    head_part, _, rest = out.partition("\n…[")
    _, _, tail_part = rest.partition("]…\n")
    assert count_tokens(head_part, "") <= head_budget
    assert count_tokens(tail_part, "") <= tail_budget


def test_no_path_omits_where_clause_but_still_truncates():
    body = _body(5000)
    out = render_head_tail_notice(body, path=None, head_tokens=30, tail_tokens=10,
                                  full_tokens=count_tokens(body, ""))
    assert "tokens elided" in out
    assert "full at" not in out      # no path → no "full at {path}" clause
    assert len(out) < len(body)


def test_deterministic_byte_stable():
    body = _body(3000)
    a = render_head_tail_notice(body, path="/x", head_tokens=40, tail_tokens=20)
    b = render_head_tail_notice(body, path="/x", head_tokens=40, tail_tokens=20)
    assert a == b


def test_output_full_tokens_accessor():
    env = parse_envelope(
        '{"status":"success","output":{"path":"/x","content_type":"text/plain",'
        '"full_tokens":9000,"full_chars":36000}}')
    assert output_full_tokens(env) == 9000
    env2 = parse_envelope('{"status":"success","output":{"path":"/x","data":"hi"}}')
    assert output_full_tokens(env2) is None
