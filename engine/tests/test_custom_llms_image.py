# -*- coding: utf-8 -*-
"""RE-3 (path model) — providers build multimodal requests from image entries
that are real file paths (a /run file already mapped by PromptNode), http URLs,
or raw bytes. OpenAI goes through convert_input (legacy ShareGPT path); Gemini
reads the source bytes into an inline-image Part.
"""

import base64
import types as _t

import pytest

import vibecanvas_engine.custom_llms as custom_llms

_PNG = b"\x89PNG\r\n\x1a\n"


def _png_file(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_PNG)
    return str(p)


def test_openai_interleaves_image_from_file(monkeypatch, tmp_path):
    captured = {}

    class _Resp:
        choices = [_t.SimpleNamespace(message=_t.SimpleNamespace(content="ok"))]

    class _Client:
        def __init__(self, **kw):
            pass

        chat = _t.SimpleNamespace(completions=_t.SimpleNamespace(
            create=lambda **kw: (captured.update(kw) or _Resp())))

    monkeypatch.setattr("openai.OpenAI", _Client, raising=False)
    m = custom_llms.OpenAIModel(model_name="gpt-4o", api_key="k", api_url="u")
    # bare <<image>> inline → convert_input interleaves at that position
    m({"conversations": [{"from": "human", "value": "see <<image>> end"}],
       "image": [_png_file(tmp_path)], "video": [], "audio": []})

    content = captured["messages"][-1]["content"]
    assert [b["type"] for b in content] == ["text", "image_url", "text"]
    url = content[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")          # encode_image label
    assert base64.b64decode(url.split(",", 1)[1]) == _PNG     # real file bytes
    # Provider-specific pixel hints are preserved by model_utils.
    assert content[1]["min_pixels"] == 1 and content[1]["max_pixels"] == 512 * 512


def test_openai_text_only_unchanged(monkeypatch):
    captured = {}

    class _Resp:
        choices = [_t.SimpleNamespace(message=_t.SimpleNamespace(content="ok"))]

    class _Client:
        def __init__(self, **kw):
            pass

        chat = _t.SimpleNamespace(completions=_t.SimpleNamespace(
            create=lambda **kw: (captured.update(kw) or _Resp())))

    monkeypatch.setattr("openai.OpenAI", _Client, raising=False)
    custom_llms.OpenAIModel("m", "k", "u")(
        {"conversations": [{"from": "human", "value": "hi"}],
         "image": [], "video": [], "audio": []})
    assert captured["messages"][-1]["content"] == "hi"      # plain string, no list


# --- Gemini ---------------------------------------------------------------

try:
    from google import genai as _genai  # noqa: F401
    _HAS_GENAI = True
except Exception:  # pragma: no cover
    _HAS_GENAI = False


def _gemini_capture(monkeypatch):
    captured = {}

    class _Resp:
        text = "ok"

    class _Models:
        def generate_content(self, **kw):
            captured.update(kw)
            return _Resp()

    class _Client:
        def __init__(self, **kw):
            captured["client_kwargs"] = kw
            self.models = _Models()

    monkeypatch.setattr("google.genai.Client", _Client, raising=False)
    return captured


@pytest.mark.skipif(not _HAS_GENAI, reason="google-genai not installed")
def test_gemini_inline_image_from_file(monkeypatch, tmp_path):
    captured = _gemini_capture(monkeypatch)
    m = custom_llms.GeminiModel(model_name="gemini-2.0-flash", api_key="k")
    m({"conversations": [{"from": "human", "value": "hi"}],
       "image": [_png_file(tmp_path)], "video": [], "audio": []})

    last = captured["contents"][-1]
    assert any(getattr(p, "text", None) for p in last.parts)
    img = next(p for p in last.parts if getattr(p, "inline_data", None) is not None)
    assert img.inline_data.mime_type == "image/png"   # mimetypes from .png ext
    assert img.inline_data.data == _PNG


@pytest.mark.skipif(not _HAS_GENAI, reason="google-genai not installed")
def test_gemini_text_only_unchanged(monkeypatch):
    captured = _gemini_capture(monkeypatch)
    custom_llms.GeminiModel(model_name="gemini-2.0-flash", api_key="k")(
        {"conversations": [{"from": "human", "value": "hi"}],
         "image": [], "video": [], "audio": []})
    last = captured["contents"][-1]
    assert all(getattr(p, "inline_data", None) is None for p in last.parts)
    assert any(getattr(p, "text", None) == "hi" for p in last.parts)


@pytest.mark.skipif(not _HAS_GENAI, reason="google-genai not installed")
def test_gemini_uses_runtime_broker_base_url(monkeypatch):
    captured = _gemini_capture(monkeypatch)
    custom_llms.GeminiModel(
        model_name="gemini-2.5-flash",
        api_key="workflow-capability",
        api_url="http://platform.test/api/internal/runtime-model/v1",
        timeout=45,
    )(
        {
            "conversations": [{"from": "human", "value": "hi"}],
            "image": [],
            "video": [],
            "audio": [],
        }
    )
    kwargs = captured["client_kwargs"]
    assert kwargs["api_key"] == "workflow-capability"
    assert kwargs["http_options"].base_url == (
        "http://platform.test/api/internal/runtime-model/v1"
    )
    assert kwargs["http_options"].timeout == 45000
