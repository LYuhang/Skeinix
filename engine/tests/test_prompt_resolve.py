"""Path model (identity bind) — PromptNode forwards image refs to the provider
VERBATIM. Inside the sandbox ``/run`` and ``/mount`` are available at those
literal paths, so a media path IS the real file and needs no mapping; http URLs
pass through too. The node does no path resolution.
"""
from vibecanvas_engine.nodes.prompt import PromptNode
import vibecanvas_engine.custom_llms as custom_llms


class _CapModel:
    captured = {}

    def __init__(self, **kw):
        pass

    def __call__(self, conversation_dict, inference_config=None, stop_event=None):
        _CapModel.captured = conversation_dict
        return "ok"


def _node(template):
    n = PromptNode.__new__(PromptNode)
    n.node_config = {
        "prompt_template": template,
        "model_name": "saved-cap",
        "inference_config": {},
    }
    return n


def _patch_provider(monkeypatch):
    monkeypatch.setitem(
        custom_llms.CUSTOM_PROVIDERS,
        "_cap",
        {"class": _CapModel, "default_model": "m", "default_url": "u"},
    )


def _runtime_extra(**values):
    """Model credentials reach the sandbox only as broker capabilities."""
    return {
        **values,
        "llm_credentials": {
            "saved-cap": {
                "provider": "_cap",
                "model_name": "capture",
                "api_key": "test-broker-capability",
                "api_url": "http://runtime-model-broker.invalid",
            },
        },
    }


def test_prompt_forwards_media_path_verbatim(monkeypatch, tmp_path):
    _patch_provider(monkeypatch)
    # A real file (the bind exposes it at this literal path inside the sandbox).
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    n = _node("Look [<<image>>]({{img}})")
    n(inputs={"img": str(img)}, previous_outputs={},
      extra=_runtime_extra(run_id="r1", run_dir=str(tmp_path)))
    assert _CapModel.captured["image"] == [str(img)]
    # And the bare placeholder is left inline for convert_input to interleave.
    assert _CapModel.captured["conversations"][0]["value"] == "Look <<image>>"


def test_prompt_forwards_logical_run_path_verbatim(monkeypatch):
    _patch_provider(monkeypatch)
    # A logical /run/<rel> path is forwarded unchanged — inside the sandbox it is
    # the real bind-mounted file; the node never rewrites it.
    n = _node("Look [<<image>>]({{img}})")
    n(inputs={"img": "/run/n1/a.png"}, previous_outputs={},
      extra=_runtime_extra(run_id="r1", run_dir="/whatever"))
    assert _CapModel.captured["image"] == ["/run/n1/a.png"]


def test_prompt_url_passthrough(monkeypatch):
    _patch_provider(monkeypatch)
    # http URL passes through; a /run path with no extra is also forwarded as-is.
    n = _node("Look [<<image>>]({{img}})")
    n(
        inputs={"img": "https://x/y.png"},
        previous_outputs={},
        extra=_runtime_extra(),
    )
    assert _CapModel.captured["image"] == ["https://x/y.png"]

    n2 = _node("Look [<<image>>]({{img}})")
    n2(
        inputs={"img": "/run/n1/a.png"},
        previous_outputs={},
        extra=_runtime_extra(),
    )
    assert _CapModel.captured["image"] == ["/run/n1/a.png"]
