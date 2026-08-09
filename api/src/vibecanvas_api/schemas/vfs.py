"""VFS 2c — read-only route response models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class VfsEntryOut(BaseModel):
    path: str
    kind: Literal["artifact", "scratch"]
    content_type: str
    abstract: str
    size_bytes: int
    wf_version: Optional[str] = None
    last_access: float
    stale: bool
    capabilities: list[Literal["read", "download", "copy_path", "rename", "delete"]]


class VfsListOut(BaseModel):
    entries: list[VfsEntryOut]
    root_capabilities: dict[str, list[Literal["upload", "create_folder", "rename", "delete"]]]


class VfsRunEntryOut(BaseModel):
    """RE-4 — run-tier list entry. Exposes path/content_type/size_bytes ONLY:
    no object_key (storage detail) and no agent-VFS fields (kind/abstract/
    last_access/stale don't apply to the ephemeral run tier)."""
    path: str
    content_type: str
    size_bytes: int
    capabilities: list[Literal["read", "download", "copy_path"]]


class VfsRunListOut(BaseModel):
    entries: list[VfsRunEntryOut]


class VfsUploadOut(BaseModel):
    """Durable VFS upload result. ``replaced`` indicates overwrite-in-place."""
    path: str
    size_bytes: int
    content_type: str
    replaced: bool


class VfsWriteIn(BaseModel):
    """Request body for ``PUT /api/v1/vfs/content``.

    This endpoint is intentionally text-only and user-managed-path-only; it is
    used by the Explorer editor to overwrite existing UTF-8 files under
    ``/mount`` or ``/data``.
    """
    wf_id: str
    path: str
    content: str
    content_type: Optional[str] = None


class VfsWriteOut(BaseModel):
    """Result of ``PUT /api/v1/vfs/content``."""
    path: str
    size_bytes: int
    content_type: str
    replaced: bool


class VfsWriteBytesIn(BaseModel):
    """Request body for ``PUT /api/v1/vfs/bytes``. `data_b64` is raw file
    bytes base64-encoded by the browser, used for binary editors such as xlsx."""
    wf_id: str
    path: str
    data_b64: str
    content_type: str = "application/octet-stream"


class VfsDeleteOut(BaseModel):
    """Result of ``DELETE /api/v1/vfs``. `deleted` is the number of rows
    removed (1 for a single file, N for a folder-prefix delete)."""
    deleted: int


class VfsRenameIn(BaseModel):
    """Request body for ``POST /api/v1/vfs/rename``."""
    wf_id: str
    old_path: str
    new_path: str


class VfsRenameOut(BaseModel):
    """Result of ``POST /api/v1/vfs/rename`` — the new path."""
    path: str


class VfsSignIn(BaseModel):
    """Request body for ``POST /api/v1/vfs/sign``. The tenant is taken from the
    auth context, NEVER from the client, so it is not part of this body."""
    path: str
    wf_id: Optional[str] = None
    run_id: Optional[str] = None


class VfsSignOut(BaseModel):
    """A short-lived signed URL the frontend can use directly as an
    ``<img src>`` / ``<video src>`` (no Authorization header needed)."""
    url: str


class VfsReadOut(BaseModel):
    path: str
    content_type: str
    content: Optional[str] = None
    size_bytes: int
    truncated: bool
    wf_version: Optional[str] = None
    run_id: Optional[str] = None
    stale: bool
