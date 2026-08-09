from vibecanvas_api.agents.tools import build_tools
from vibecanvas_api.services.platform_mcp.browser_tools import BROWSER_TOOLS

_BROWSER_NAMES = {t.name for t in BROWSER_TOOLS}


def _names(tools):
    return {getattr(t, "name", getattr(t, "__name__", "")) for t in tools}


def test_langchain_private_tools_never_register_platform_browser_tools():
    assert not (_BROWSER_NAMES & _names(build_tools(set())))
    assert not (_BROWSER_NAMES & _names(build_tools({"build"})))
    assert not (_BROWSER_NAMES & _names(build_tools({"browser"})))
    assert _names(build_tools({"browser"})) == _names(build_tools(set()))


def test_mode_literal_accepts_browser():
    from vibecanvas_api.schemas.chat import MessagePostBody
    body = MessagePostBody(content="hi", mode="browser")
    assert body.mode == "browser"
