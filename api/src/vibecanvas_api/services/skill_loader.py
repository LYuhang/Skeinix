from __future__ import annotations
import yaml


class SkillParseError(ValueError):
    ...


def parse_skill_md(text: str) -> tuple[dict, str]:
    """Split a SKILL.md into (frontmatter_dict, body). Frontmatter = leading YAML
    between the first pair of '---' fences. Normalizes 'allowed-tools' ->
    'allowed_tools'. Requires non-empty name + description; allowed_tools must be a list."""
    s = text.lstrip()
    if not s.startswith("---"):
        raise SkillParseError("SKILL.md must start with a YAML frontmatter '---' block")
    rest = s[3:]
    end = rest.find("\n---")
    if end == -1:
        raise SkillParseError("unterminated frontmatter block")
    fm_raw, body = rest[:end], rest[end + 4:]
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError as e:
        raise SkillParseError(f"invalid frontmatter YAML: {e}") from e
    if not isinstance(fm, dict):
        raise SkillParseError("frontmatter must be a mapping")
    if "allowed-tools" in fm and "allowed_tools" not in fm:
        fm["allowed_tools"] = fm.pop("allowed-tools")
    fm.setdefault("allowed_tools", [])
    fm.setdefault("version", 1)
    name, desc = fm.get("name"), fm.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillParseError("frontmatter 'name' is required")
    if not isinstance(desc, str) or not desc.strip():
        raise SkillParseError("frontmatter 'description' is required")
    if not isinstance(fm["allowed_tools"], list):
        raise SkillParseError("'allowed-tools' must be a list")
    return fm, body.lstrip("\n")
