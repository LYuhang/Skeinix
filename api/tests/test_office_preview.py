from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibecanvas_api.services import office_preview


def test_office_preview_converts_once_and_reuses_bounded_disk_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(office_preview, "_CACHE_ROOT", tmp_path / "cache")
    monkeypatch.setattr(office_preview.shutil, "which", lambda _name: "/usr/bin/soffice")

    def fake_run(arguments, **_kwargs):
        calls.append(arguments)
        source = Path(arguments[-1])
        source.with_suffix(".pdf").write_bytes(b"%PDF-1.7\nrendered")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(office_preview.subprocess, "run", fake_run)
    source = b"PK\x03\x04office"

    first = office_preview.render_office_preview_pdf(source, ".docx")
    second = office_preview.render_office_preview_pdf(source, ".docx")

    assert first == second == b"%PDF-1.7\nrendered"
    assert len(calls) == 1
    assert "--headless" in calls[0]
    assert list((tmp_path / "cache").glob("*.pdf"))


def test_office_preview_rejects_unsupported_source_type() -> None:
    with pytest.raises(
        office_preview.OfficePreviewError,
        match="unsupported_office_preview_type",
    ):
        office_preview.render_office_preview_pdf(b"content", ".xls")
