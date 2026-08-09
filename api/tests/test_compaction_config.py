"""Agent compaction-v2 configuration knobs."""
from vibecanvas_api.config import AgentConfig


def test_compaction_v2_defaults():
    c = AgentConfig({}).compaction_v2
    assert c.inline_chars == 16000
    assert c.protect_recent_rounds == 3
    assert c.pressure_abstract == 0.5
    assert c.pressure_summary == 0.8
    assert c.hysteresis_target == 0.5
    assert c.resegment_every_rounds == 8
    assert c.clear_at_least == 20000
    assert c.pin_first_exchange is True
    assert c.error_protect_rounds is None
    assert c.compress_single_tokens == 30000
    assert c.compress_pressure == 0.8
    assert c.aux_full_rounds == 2
    assert c.summarizer_version == "v1"
    assert c.v2_enabled is False
    # size tiers: `none` band below S never decays (full_rounds is None)
    by_name = {t["name"]: t for t in c.size_tiers}
    assert by_name["none"]["full_rounds"] is None
    assert by_name["S"]["full_rounds"] == 8
    assert by_name["XL"]["max_tokens"] is None
    assert "read_file" in c.stale_on_reread_tools


def test_compaction_v2_overrides():
    raw = {"compaction": {"v2": {
        "protect_recent_rounds": 5,
        "clear_at_least": 50000,
        "stale_on_reread_tools": ["get_workflow"],
        "v2_enabled": True,
    }}}
    c = AgentConfig(raw).compaction_v2
    assert c.protect_recent_rounds == 5
    assert c.clear_at_least == 50000
    assert c.stale_on_reread_tools == ["get_workflow"]
    assert c.v2_enabled is True
    assert c.inline_chars == 16000  # untouched default


def test_compaction_v2_coexists_with_legacy_knobs():
    # The legacy s2a/s2b flat knobs still load from the same `compaction` block.
    a = AgentConfig({"compaction": {"s2a_oversize_tokens": 9999, "v2": {"inline_chars": 5}}})
    assert a.s2a_oversize_tokens == 9999
    assert a.compaction_v2.inline_chars == 5
