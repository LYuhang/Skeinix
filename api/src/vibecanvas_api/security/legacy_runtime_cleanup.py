"""One-way cleanup for removed plaintext Agent Runtime credential layouts."""

from __future__ import annotations

import os
import stat


def purge_legacy_codex_auth_files(runtime_root: str) -> int:
    """Delete only the removed ``<tenant>/<user>/.codex/auth.json`` layout.

    The current API-backed Codex Runtime uses a Chat-scoped state directory and
    the host Model Broker. Optional account login uses the distinct
    ``codex-account-v1/.codex`` directory, so this old path has no valid
    consumer. Directory symlinks are never followed.
    """
    root = os.path.abspath(str(runtime_root or ""))
    if not root or not os.path.isdir(root):
        return 0
    removed = 0
    with os.scandir(root) as tenants:
        for tenant in tenants:
            if tenant.is_symlink() or not tenant.is_dir(follow_symlinks=False):
                continue
            with os.scandir(tenant.path) as users:
                for user in users:
                    if user.is_symlink() or not user.is_dir(follow_symlinks=False):
                        continue
                    auth_path = os.path.join(user.path, ".codex", "auth.json")
                    try:
                        metadata = os.lstat(auth_path)
                    except FileNotFoundError:
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        raise RuntimeError("legacy Codex auth path is a directory")
                    os.unlink(auth_path)
                    removed += 1
    return removed


__all__ = ["purge_legacy_codex_auth_files"]
