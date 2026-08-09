from __future__ import annotations

from vibecanvas_api.services.kb_summary import (
    MAX_KB_SUMMARY_CHARS,
    summarize_knowledge,
)


def test_extractive_summary_is_local_bounded_and_normalized(monkeypatch) -> None:
    monkeypatch.setenv("KB_SUMMARY_MODE", "extractive")
    summary = summarize_knowledge(["  Alpha\n\nBeta  ", "Gamma " * 200])
    assert summary.startswith("Alpha Beta")
    assert "\n" not in summary
    assert len(summary) <= MAX_KB_SUMMARY_CHARS
