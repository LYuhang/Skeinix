from __future__ import annotations

import os

from vibecanvas_api.security.legacy_runtime_cleanup import (
    purge_legacy_codex_auth_files,
)


def test_plaintext_codex_auth_cleanup_is_exact_and_does_not_follow_symlinks(
    tmp_path,
):
    root = tmp_path / "runtime"
    legacy = root / "tenant" / "user" / ".codex"
    legacy.mkdir(parents=True)
    auth = legacy / "auth.json"
    auth.write_text("secret", encoding="utf-8")
    retained = legacy / "sessions.jsonl"
    retained.write_text("thread state", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_auth = outside / "auth.json"
    outside_auth.write_text("outside-secret", encoding="utf-8")
    os.symlink(outside, root / "symlink-tenant")

    assert purge_legacy_codex_auth_files(str(root)) == 1
    assert not auth.exists()
    assert retained.read_text(encoding="utf-8") == "thread state"
    assert outside_auth.read_text(encoding="utf-8") == "outside-secret"
