"""The /browser command context — the v1 control-only BROWSER block. Per single-
source-of-truth it states the control + safety discipline and names NO tools
(the browser_* tools self-describe via their docstrings)."""
from vibecanvas_api.agents.commands import command_context_for
from vibecanvas_api.agents.prompts.compose import build_system_prompt


def _bsp(mode="chat"):
    modes = {"browser"} if mode == "browser" else set()
    return build_system_prompt(modes)


def _browser_context():
    return command_context_for("browser")


def test_browser_prompt_has_control_discipline():
    p = _browser_context()
    assert "## Browser mode" in p
    assert "See before you act" in p                  # observe-before-act
    assert "ONE action at a time" in p                # serial / dependent actions
    assert "EXPECT" in p                              # wait-for post-condition
    assert "STOP" in p                                 # instant-halt awareness
    # shared addressing background is explained (tab id + element handle)
    assert "tab id" in p and "handle" in p
    # single source of truth: the control block names no browser_* tools
    assert "browser_snapshot" not in p


def test_browser_prompt_has_recovery_discipline():
    """An error/empty result is a signal to retry differently, not to stop."""
    p = _browser_context()
    assert "Recover, don't quit" in p                  # persistence section present
    assert "no result captured" in p                   # empty-read recovery named
    assert "press Enter" in p                           # typed-but-no-nav recovery
    # don't bail after the first error — only stop when genuinely blocked
    assert "before giving up" in p
    assert "never stop silently after a single error" in p


def test_chat_prompt_has_no_browser_section():
    assert "## Browser mode" not in _bsp("chat")


def test_active_browser_does_not_change_system_prompt():
    assert _bsp("browser") == _bsp("chat")
