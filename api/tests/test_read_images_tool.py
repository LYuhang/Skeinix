from types import SimpleNamespace

import pytest

from vibecanvas_api.agents.tools.media.read_images import _do_read_images


class _Ctx:
    wf_id = "wf_x"
    pending_images = None
    vfs = None


class _Vfs:
    def read_bytes(self, *, wf_id, path):
        if path == "/data/pixel.png":
            return (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
                b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02"
                b"\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDAT"
                b"\x08\xd7c\xf8\xff\xff?\x00\x05\xfe\x02\xfeA"
                b"\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
            )
        return None


def _runtime(ctx=None):
    return SimpleNamespace(context=ctx or _Ctx())


@pytest.mark.asyncio
async def test_read_images_rejects_http_urls_with_actionable_error():
    content, artifact = await _do_read_images(
        ["https://example.com/image.png"],
        runtime=_runtime(),
    )

    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "no_images"
    assert "URLs are not supported" in content
    assert "Save the image to a local path" in content
    assert "accessible in the current environment" in content


@pytest.mark.asyncio
async def test_read_images_reports_url_errors_when_other_images_load():
    ctx = _Ctx()
    ctx.vfs = _Vfs()
    content, artifact = await _do_read_images(
        ["/data/pixel.png", "https://example.com/image.png"],
        runtime=_runtime(ctx),
    )

    assert artifact["status"] == "success"
    assert "Loaded 1 image(s)" in content
    assert "URLs are not supported" in content
    assert len(ctx.pending_images) == 1
