"""Pagination schemas: Page[T] roundtrips JSON; PageRequest clamps."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from vibecanvas_api.schemas.pagination import Page, PageRequest


class Item(BaseModel):
    id: str
    name: str


def test_page_serializes():
    p = Page[Item](
        items=[Item(id="a", name="A"), Item(id="b", name="B")],
        total=42, limit=10, offset=0,
    )
    out = p.model_dump()
    assert out == {
        "items": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
        "total": 42, "limit": 10, "offset": 0,
    }


def test_page_request_defaults():
    r = PageRequest()
    assert r.limit == 50 and r.offset == 0


def test_page_request_rejects_negative_offset():
    with pytest.raises(ValidationError):
        PageRequest(limit=10, offset=-1)


def test_page_request_rejects_too_high_limit():
    with pytest.raises(ValidationError):
        PageRequest(limit=10_000, offset=0)
