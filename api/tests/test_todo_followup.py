from vibecanvas_api.agents.prompts.compose import build_system_prompt


def test_system_prompt_explains_special_protocol():
    prompt = build_system_prompt()
    assert "## Platform message protocol" in prompt
    assert "<system-reminder>" in prompt
    assert "<todo-reminder>" in prompt
    assert "slash command" in prompt
