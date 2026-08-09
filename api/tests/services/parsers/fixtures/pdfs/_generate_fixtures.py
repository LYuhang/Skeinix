"""Regenerate PDF fixtures used by tests/services/parsers/test_pdf.py.

Run from anywhere with the vibecanvas_next env active:

    $PY tests/services/parsers/fixtures/pdfs/_generate_fixtures.py

Produces (in the same directory):
    one_page.pdf    — 1 text page ("page one")
    two_pages.pdf   — 2 text pages ("page one", "page two")
    encrypted.pdf   — password-protected ("secret") clone of two_pages.pdf
    image_only.pdf  — single blank page, no text layer (triggers EmptyDocument)

reportlab is required only for fixture generation, not at runtime;
do NOT add it to pyproject.toml.
"""
from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent


def make_one_page() -> None:
    p = HERE / "one_page.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(100, 750, "page one")
    c.showPage()
    c.save()


def make_two_pages() -> None:
    p = HERE / "two_pages.pdf"
    c = canvas.Canvas(str(p))
    c.drawString(100, 750, "page one")
    c.showPage()
    c.drawString(100, 750, "page two")
    c.showPage()
    c.save()


def make_encrypted() -> None:
    src = HERE / "two_pages.pdf"
    dst = HERE / "encrypted.pdf"
    r = PdfReader(str(src))
    w = PdfWriter(clone_from=r)
    w.encrypt("secret")
    with open(dst, "wb") as f:
        w.write(f)


def make_image_only() -> None:
    """A blank PDF — no drawString calls, so extract_text() returns ''."""
    p = HERE / "image_only.pdf"
    w = PdfWriter()
    w.add_blank_page(width=595, height=842)
    with open(p, "wb") as f:
        w.write(f)


def main() -> None:
    make_one_page()
    make_two_pages()
    make_encrypted()
    make_image_only()
    for name in ("one_page.pdf", "two_pages.pdf", "encrypted.pdf", "image_only.pdf"):
        size = (HERE / name).stat().st_size
        print(f"  {name}: {size} bytes")


if __name__ == "__main__":
    main()
