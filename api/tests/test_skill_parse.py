import pytest
from vibecanvas_api.services.skill_loader import parse_skill_md, SkillParseError

def test_parses_frontmatter_and_body():
    md = '---\nname: greet\ndescription: say hi\nallowed-tools: [canvas, "mcp:x"]\nversion: 2\n---\n# Body\nhello'
    fm, body = parse_skill_md(md)
    assert fm["name"] == "greet" and fm["description"] == "say hi"
    assert fm["allowed_tools"] == ["canvas", "mcp:x"] and fm["version"] == 2
    assert body.strip().startswith("# Body")

def test_missing_fields_raise():
    with pytest.raises(SkillParseError):
        parse_skill_md("---\ndescription: x\n---\nbody")     # no name
    with pytest.raises(SkillParseError):
        parse_skill_md("no frontmatter")
