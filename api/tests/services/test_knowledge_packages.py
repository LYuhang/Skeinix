from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

import pytest

from vibecanvas_api.services.knowledge_packages import (
    MAX_PACKAGE_BYTES,
    PackageFile,
    normalize_imported_package,
    normalize_package_path,
    package_files_from_zip,
    resolve_package_content_type,
    validate_package,
)


def _file(path: str, data: bytes = b"x") -> PackageFile:
    return PackageFile(path=path, data=data, content_type="application/octet-stream")


def test_package_requires_root_readme_and_preserves_hierarchy() -> None:
    package = validate_package([
        _file("README.md", b"# Package"),
        _file("notes/evaluation.md"),
        _file("media/architecture.png"),
    ])
    assert [item.path for item in package] == [
        "README.md",
        "notes/evaluation.md",
        "media/architecture.png",
    ]

    with pytest.raises(ValueError, match="README.md"):
        validate_package([_file("notes/readme.md")])


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "notes/../../secret",
        "/absolute.txt",
        "notes//file.txt",
        "notes/bad\nname.md",
    ],
)
def test_package_path_rejects_escape_and_ambiguous_segments(path: str) -> None:
    with pytest.raises(ValueError, match="invalid Knowledge package path"):
        normalize_package_path(path)


def test_package_rejects_case_insensitive_duplicate_paths() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        validate_package([
            _file("README.md"),
            _file("Notes/Result.txt"),
            _file("notes/result.TXT"),
        ])


def test_package_enforces_total_size_without_restricting_file_type() -> None:
    validate_package([
        _file("README.md"),
        _file("media/reference.custom-format", b"binary"),
    ])
    with pytest.raises(ValueError, match="total bytes"):
        validate_package([
            _file("README.md"),
            _file("large.bin", b"x" * MAX_PACKAGE_BYTES),
        ])


def test_folder_import_removes_one_transport_wrapper() -> None:
    package = normalize_imported_package([
        _file("team-handbook/README.md", b"# Team handbook"),
        _file("team-handbook/policies/leave.pdf", b"%PDF-1.7"),
    ])
    assert [item.path for item in package] == [
        "README.md",
        "policies/leave.pdf",
    ]


def test_folder_import_does_not_accept_a_nested_readme_as_the_root() -> None:
    with pytest.raises(ValueError, match="root must contain README.md"):
        normalize_imported_package([
            _file("notes/README.md"),
            _file("media/README.md"),
        ])


def test_zip_import_preserves_files_and_rejects_path_traversal() -> None:
    valid = BytesIO()
    with ZipFile(valid, "w") as archive:
        archive.writestr("research/README.md", "# Research")
        archive.writestr("research/papers/model.pdf", b"%PDF-1.7")
    package = package_files_from_zip(valid.getvalue())
    assert [item.path for item in package] == ["README.md", "papers/model.pdf"]

    unsafe = BytesIO()
    with ZipFile(unsafe, "w") as archive:
        archive.writestr("../README.md", "escape")
    with pytest.raises(ValueError, match="invalid Knowledge package path"):
        package_files_from_zip(unsafe.getvalue())


@pytest.mark.parametrize(
    ("path", "data", "declared", "expected"),
    [
        ("docs/report.pdf", b"%PDF-1.7\n", "application/octet-stream", "application/pdf"),
        (
            "slides/deck.pptx",
            b"PK\x03\x04pptx",
            "application/octet-stream",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ),
        ("images/diagram.png", b"\x89PNG\r\n\x1a\n", "application/octet-stream", "image/png"),
        ("media/brief.mp3", b"ID3audio", "application/octet-stream", "audio/mpeg"),
        ("media/demo.mp4", b"\x00\x00\x00\x18ftypmp42", "application/octet-stream", "video/mp4"),
        ("notes/guide.md", b"# Guide\n", "text/plain", "text/markdown"),
        ("tables/metrics.csv", b"name,value\na,1\n", "text/plain", "table/csv"),
        (
            "design/source.drawio",
            b"<mxfile/>",
            "application/xml",
            "application/vnd.jgraph.mxfile",
        ),
    ],
)
def test_package_content_type_uses_central_format_contract(
    path: str,
    data: bytes,
    declared: str,
    expected: str,
) -> None:
    assert resolve_package_content_type(path, data, declared) == expected
