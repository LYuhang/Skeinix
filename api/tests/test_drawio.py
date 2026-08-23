from __future__ import annotations

from vibecanvas_api.drawio import inspect_drawio


VALID = b'''<mxfile><diagram id="page-1" name="Page 1"><mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="a" vertex="1" parent="1"/>
  <mxCell id="b" vertex="1" parent="1"/>
  <mxCell id="a-b" edge="1" source="a" target="b" parent="1"/>
</root></mxGraphModel></diagram></mxfile>'''


def test_native_drawio_inspection_reports_structure_and_hash() -> None:
    result = inspect_drawio(VALID)

    assert result.valid is True
    assert result.pages == 1
    assert result.vertices == 2
    assert result.edges == 1
    assert result.source_hash.startswith("sha256:")
    assert result.preview_metadata()["status"] == "valid"


def test_native_drawio_inspection_rejects_unsafe_or_dangling_xml() -> None:
    unsafe = inspect_drawio(b'<!DOCTYPE foo><mxGraphModel/>')
    assert unsafe.valid is False
    assert unsafe.issues[0]["code"] == "unsafe-xml-declaration"

    dangling = inspect_drawio(b'''<mxGraphModel><root>
      <mxCell id="0"/><mxCell id="1" parent="0"/>
      <mxCell id="edge" edge="1" source="missing" parent="1"/>
    </root></mxGraphModel>''')
    assert dangling.valid is False
    assert dangling.issues[0]["code"] == "dangling-drawio-terminal"


def test_native_drawio_inspection_rejects_duplicate_ids_and_wrong_root() -> None:
    duplicate = inspect_drawio(
        b'<mxGraphModel><root><mxCell id="0"/><mxCell id="0"/></root></mxGraphModel>'
    )
    assert duplicate.valid is False
    assert duplicate.issues[0]["code"] == "duplicate-drawio-cell-id"

    wrong_root = inspect_drawio(b"<svg/>")
    assert wrong_root.valid is False
    assert wrong_root.issues[0]["code"] == "invalid-drawio-root"
