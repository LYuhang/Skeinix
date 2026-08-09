# -*- coding: utf-8 -*-
"""Credential-free baseline workflow snapshot tests."""
import pytest

def test_snapshot_rejects_rootless_provider(monkeypatch):
    from vibecanvas_api.services.sandbox import lifecycle as lc

    monkeypatch.setattr(
        lc,
        "get_sandbox_provider",
        lambda: type("Rootless", (), {"_rootless": True})(),
    )
    with pytest.raises(RuntimeError, match="requires rootful"):
        lc.SnapshotLifecycle().acquire(runs_root="/r", concurrency=2)
