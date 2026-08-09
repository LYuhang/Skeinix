from __future__ import annotations

import importlib


web_search_module = importlib.import_module(
    "vibecanvas_api.agents.tools.web.web_search"
)


def test_duckduckgo_html_search_returns_source_urls(
    monkeypatch,
) -> None:
    html = """
    <div class="result">
      <a class="result__a"
         href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Freport">
        Example report
      </a>
      <div class="result__snippet">A useful result.</div>
    </div>
    """

    class Response:
        text = html

        def raise_for_status(self) -> None:
            return None

    captured: dict = {}

    def fake_get(url: str, **kwargs):
        captured.update({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(web_search_module.requests, "get", fake_get)
    results = web_search_module._duckduckgo("agent frameworks", 3, 7)

    assert captured["url"] == "https://html.duckduckgo.com/html/"
    assert captured["params"] == {"q": "agent frameworks"}
    assert captured["timeout"] == 7
    assert results == [
        {
            "title": "Example report",
            "url": "https://example.com/report",
            "snippet": "A useful result.",
            "score": None,
        }
    ]
