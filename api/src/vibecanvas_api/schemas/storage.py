"""Logical Storage namespace response/request models."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

from vibecanvas_api.schemas.access import ResourceAccessOut


class StorageItem(BaseModel):
    name: str
    path: str
    kind: Literal["file", "folder"]
    size_bytes: int | None = None
    modified_at: Optional[str] = None
    content_type: Optional[str] = None
    source: Optional[str] = None
    can_create_child: bool = False
    can_rename: bool = False
    can_delete: bool = False
    can_write: bool = False
    access: ResourceAccessOut | None = None


class StorageListOut(BaseModel):
    path: str
    items: list[StorageItem]
    next_cursor: Optional[str] = None
    total_estimate: int | None = None
    readonly: bool = False
    access: ResourceAccessOut | None = None


class StorageReadOut(BaseModel):
    path: str
    content_type: str
    content: Optional[str] = None
    size_bytes: int
    truncated: bool = False
    access: ResourceAccessOut | None = None


class StorageWriteIn(BaseModel):
    path: str
    content: str
    content_type: Optional[str] = None


class StorageWriteOut(BaseModel):
    path: str
    size_bytes: int
    content_type: str
    replaced: bool
    access: ResourceAccessOut | None = None


class StorageMkdirIn(BaseModel):
    path: str


class StorageDeleteOut(BaseModel):
    deleted: int
    access: ResourceAccessOut | None = None


class StorageRenameIn(BaseModel):
    old_path: str
    new_path: str


class StorageRenameOut(BaseModel):
    path: str
    access: ResourceAccessOut | None = None
