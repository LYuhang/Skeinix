from __future__ import annotations

import stat

from vibecanvas_api.services.user_mount_workspace import _flush_files


def test_ephemeral_user_mount_files_are_private(tmp_path) -> None:
    root = tmp_path / "mount"
    target = root / "nested" / "context.txt"

    assert _flush_files([(str(target), b"private")]) == 1

    assert stat.S_IMODE((root / "nested").stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_bytes() == b"private"
