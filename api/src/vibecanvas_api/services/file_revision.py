"""Stable optimistic-concurrency revisions for object-backed VFS rows."""
from __future__ import annotations

import hashlib
from typing import Any


def vfs_content_revision(content_revision: str) -> str:
    """Project the private storage token to the stable public revision shape."""
    source = f"content:{content_revision}"
    return f"sha256:{hashlib.sha256(source.encode()).hexdigest()}"


def vfs_row_revision(row: Any) -> str:
    """Return a revision that changes whenever the stored VFS row is replaced."""
    content_revision = getattr(row, "content_revision", None)
    if content_revision:
        return vfs_content_revision(str(content_revision))
    # Compatibility fallback for an unmigrated/mock row. Production rows have
    # content_revision after migration 052.
    updated = getattr(row, "last_access", None) or getattr(row, "created_at", None)
    stamp = updated.isoformat() if hasattr(updated, "isoformat") else str(updated or "")
    source = (
        f"{getattr(row, 'object_key', '')}:"
        f"{getattr(row, 'size_bytes', 0)}:{stamp}"
    )
    return f"sha256:{hashlib.sha256(source.encode()).hexdigest()}"
