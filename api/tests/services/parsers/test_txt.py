"""Tests for TxtParser — encoding fallback + empty handling."""
from __future__ import annotations

import pytest

from vibecanvas_api.services.parsers.base import EmptyDocumentError
from vibecanvas_api.services.parsers.txt import TxtParser


def test_txt_utf8():
    blob = "hello world\n\nsecond paragraph".encode("utf-8")
    segments = TxtParser().parse(blob)
    assert len(segments) == 1
    assert segments[0].text == "hello world\n\nsecond paragraph"


def test_txt_gbk_fallback():
    blob = "中文测试".encode("gbk")
    segments = TxtParser().parse(blob)
    assert "中文测试" in segments[0].text


def test_txt_empty():
    with pytest.raises(EmptyDocumentError):
        TxtParser().parse(b"")
