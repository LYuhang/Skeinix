"""Contract tests for the Playwright-backed /browser instruction block."""

from vibecanvas_api.agents.commands import COMMAND_MODES, command_context_for
from vibecanvas_api.agents.prompts.compose import build_system_prompt
from vibecanvas_api.browser.playwright_contract import (
    PLAYWRIGHT_AGENT_TOOLS,
    PLAYWRIGHT_AUDITED_UPSTREAM_TOOLS,
    PLAYWRIGHT_FORBIDDEN_TOOLS,
)


def _system_prompt(mode: str = "chat") -> str:
    return build_system_prompt({"browser"} if mode == "browser" else set())


def test_browser_prompt_teaches_official_playwright_contract() -> None:
    prompt = command_context_for("browser")
    assert "## Browser mode" in prompt
    assert "official Playwright MCP" in prompt
    assert "Observe → act → verify" in prompt
    assert "browser_snapshot" in prompt
    assert "exact target refs" in prompt
    assert "post-action page snapshot" in prompt
    assert "browser_file_upload" in prompt
    assert "file-chooser modal" in prompt
    assert "signed expiring CDN URL" in prompt
    assert "Reopen or reload" in prompt


def test_browser_prompt_does_not_teach_legacy_browser_protocol() -> None:
    prompt = command_context_for("browser")
    for legacy_name in (
        "browser_start_session",
        "browser_session_status",
        "browser_read_text",
        "browser_query",
        "browser_get_html",
        "browser_insert_rich_text",
        "browser_replace_rich_text",
        "browser_upload_file",
    ):
        assert legacy_name not in prompt
        assert legacy_name not in COMMAND_MODES["browser"].tools
    assert "element handle" not in prompt.lower()
    assert "stable tab id" not in prompt.lower()


def test_browser_command_exports_exact_reviewed_playwright_surface() -> None:
    assert COMMAND_MODES["browser"].tools == list(PLAYWRIGHT_AGENT_TOOLS)
    assert PLAYWRIGHT_FORBIDDEN_TOOLS.isdisjoint(COMMAND_MODES["browser"].tools)
    assert len(PLAYWRIGHT_AUDITED_UPSTREAM_TOOLS) == 24
    assert set(PLAYWRIGHT_AGENT_TOOLS) | PLAYWRIGHT_FORBIDDEN_TOOLS == (
        PLAYWRIGHT_AUDITED_UPSTREAM_TOOLS
    )
    assert {
        "browser_handle_dialog",
        "browser_resize",
        "browser_close",
    }.issubset(PLAYWRIGHT_AGENT_TOOLS)
    assert {
        "browser_reload",
        "browser_press_sequentially",
        "browser_mouse_wheel",
    }.isdisjoint(PLAYWRIGHT_AGENT_TOOLS)


def test_browser_prompt_has_fail_closed_safety_and_recovery() -> None:
    prompt = command_context_for("browser")
    assert "do not replay a write blindly" in prompt
    assert "unrestricted upstream evaluate/run-code tools are intentionally unavailable" in prompt
    assert "credentials, CAPTCHA, payment, publication" in prompt
    assert "Never adopt another tab, window, Chat, or user's browser" in prompt


def test_chat_prompt_has_no_browser_section() -> None:
    assert "## Browser mode" not in _system_prompt("chat")


def test_active_browser_does_not_change_base_system_prompt() -> None:
    assert _system_prompt("browser") == _system_prompt("chat")
