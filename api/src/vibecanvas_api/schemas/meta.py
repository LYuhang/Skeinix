"""Schemas for /api/v1/meta endpoints."""

from __future__ import annotations

from pydantic import BaseModel


class VersionOut(BaseModel):
    engine: str
    api: str


class MeOut(BaseModel):
    username: str


class EnumsOut(BaseModel):
    """Shape mirrors what legacy enums.get_frontend_enums() returns —
    keys vary so use dict-as-payload."""
    enums: dict
