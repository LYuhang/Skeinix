"""The composer's skill catalog section."""
from vibecanvas_api.agents.prompts.compose import build_system_prompt


def test_skills_section_present_when_catalog():
    p = build_system_prompt(skill_catalog=[{
        "name": "greet",
        "description": "say hi",
        "root_path": "/skills/skill-1",
    }])
    assert "greet" in p and "say hi" in p
    assert "Available skills" in p
    assert "/skills/skill-1/SKILL.md" in p
    assert "load_skill" not in p
    assert "list_available_skills" not in p


def test_no_section_when_empty():
    assert "Available skills" not in build_system_prompt(skill_catalog=[])
    assert "Available skills" not in build_system_prompt()
