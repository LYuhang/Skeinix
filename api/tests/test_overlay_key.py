# -*- coding: utf-8 -*-
"""Tests for the content-addressed Python-library overlay key (DB-free)."""

import re

from vibecanvas_api.services.env.overlay_key import (
    compute_overlay_key,
    parse_install_specs,
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def test_parse_keeps_versions():
    assert parse_install_specs("pandas==2.1.4\nnumpy>=1.26  # comment\n\n") == [
        "numpy>=1.26",
        "pandas==2.1.4",
    ]


def test_parse_skips_options_and_blanks():
    text = "-r x.txt\n--index-url https://example.com/simple\n\n# comment\npandas==2.1.4\n"
    assert parse_install_specs(text) == ["pandas==2.1.4"]


def test_parse_lowercases_name_keeps_spec():
    assert parse_install_specs("Pandas==2.1.4") == ["pandas==2.1.4"]


def test_parse_strips_extras_and_markers():
    assert parse_install_specs("requests[security]>=2 ; python_version>'3'") == [
        "requests>=2"
    ]


def test_key_stable_and_order_insensitive():
    a = compute_overlay_key("pandas==2.1.4\nnumpy>=1.26")
    b = compute_overlay_key("numpy>=1.26\npandas==2.1.4")
    assert a == b
    assert len(a) == 64
    assert _HEX64.match(a)


def test_key_differs_by_version():
    assert compute_overlay_key("pandas==2.1.4") != compute_overlay_key("pandas==2.2.0")


def test_empty():
    assert parse_install_specs("") == []
    assert parse_install_specs(None) == []
    k = compute_overlay_key("")
    assert len(k) == 64
    assert _HEX64.match(k)
