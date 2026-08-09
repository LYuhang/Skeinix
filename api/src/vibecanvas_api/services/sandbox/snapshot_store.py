"""Private, bounded storage shared by sandbox snapshot kinds."""

from __future__ import annotations

import os
import stat
from collections.abc import Iterable

from vibecanvas_api.config import config
from vibecanvas_api.services.sandbox.session_lifecycle import SnapshotKind


def snapshot_storage_root() -> str:
    root = os.path.abspath(config.sandbox_snapshot_root)
    os.makedirs(root, mode=0o700, exist_ok=True)
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("SANDBOX_SNAPSHOT_ROOT must be a real directory")
    if info.st_uid != os.geteuid():
        raise PermissionError("SANDBOX_SNAPSHOT_ROOT must be owned by sandboxd")
    os.chmod(root, 0o700)
    return root


def snapshot_category_root(kind: SnapshotKind | str) -> str:
    snapshot_kind = SnapshotKind(kind)
    category = (
        "baselines" if snapshot_kind == SnapshotKind.BASELINE else "sessions"
    )
    root = os.path.join(snapshot_storage_root(), category)
    os.makedirs(root, mode=0o700, exist_ok=True)
    info = os.lstat(root)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ValueError("snapshot category must be a real directory")
    if info.st_uid != os.geteuid():
        raise PermissionError("snapshot category must be owned by sandboxd")
    os.chmod(root, 0o700)
    return root


def snapshot_entries() -> list[str]:
    """Return all baseline/session entries for global count and byte limits."""

    root = snapshot_storage_root()
    entries: list[str] = []
    for category in ("baselines", "sessions"):
        category_root = os.path.join(root, category)
        if not os.path.isdir(category_root) or os.path.islink(category_root):
            continue
        entries.extend(_real_directories(category_root))
    # Count legacy flat entries until an operator removes or migrates them.
    entries.extend(
        path
        for path in _real_directories(root)
        if os.path.basename(path) not in {"baselines", "sessions"}
    )
    return entries


def snapshot_tree_bytes(roots: Iterable[str]) -> int:
    total = 0
    for root in roots:
        for current, directories, files in os.walk(root, followlinks=False):
            directories[:] = [
                name
                for name in directories
                if not os.path.islink(os.path.join(current, name))
            ]
            for name in files:
                path = os.path.join(current, name)
                if not os.path.islink(path):
                    total += os.lstat(path).st_size
    return total


def _real_directories(root: str) -> list[str]:
    return [
        os.path.join(root, name)
        for name in os.listdir(root)
        if os.path.isdir(os.path.join(root, name))
        and not os.path.islink(os.path.join(root, name))
    ]


__all__ = [
    "snapshot_category_root",
    "snapshot_entries",
    "snapshot_storage_root",
    "snapshot_tree_bytes",
]
