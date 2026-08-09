"""Derive least-privilege VFS read rules from stored HTML definitions."""

from __future__ import annotations

from html.parser import HTMLParser
import posixpath
import re
from urllib.parse import unquote, urlsplit


_ROOTS = ("/data/", "/memory/", "/logs/", "/mount/", "/run/")
_QUOTED_LOCAL_PATH = re.compile(
    r"""(?P<quote>["'`])(?P<path>/(?:data|memory|logs|mount|run)/.*?)(?P=quote)""",
    re.DOTALL,
)
_CSS_URL = re.compile(
    r"""url\(\s*(?:["']?)(?P<path>/(?:data|memory|logs|mount|run)/[^)"']+)"""
)
_DYNAMIC_MARKERS = ("${", "{{", "<%", "' +", '" +', "` +")


class _ResourceAttributeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paths: list[str] = []

    def handle_starttag(self, _tag: str, attrs: list[tuple[str, str | None]]) -> None:
        for name, value in attrs:
            if name.lower() in {"src", "href", "poster"} and value:
                self.paths.append(value)
            elif name.lower() == "style" and value:
                self.paths.extend(match.group("path") for match in _CSS_URL.finditer(value))


def _rule_from_candidate(candidate: str) -> str | None:
    value = unquote(candidate.strip())
    if not value.startswith(_ROOTS):
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    path = parsed.path
    if (
        not path.startswith(_ROOTS)
        or "\x00" in path
        or "\\" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        return None

    marker_positions = [
        position
        for marker in _DYNAMIC_MARKERS
        if (position := path.find(marker)) >= 0
    ]
    is_dynamic = bool(marker_positions)
    if marker_positions:
        path = path[:min(marker_positions)]
        # Dynamic expressions receive only the statically declared directory.
        if not path.endswith("/"):
            path = path.rsplit("/", 1)[0] + "/"

    is_prefix = path.endswith("/")
    normalized = posixpath.normpath(path)
    if not normalized.startswith(_ROOTS):
        return None
    # A dynamic expression such as ``/data/${userInput}`` does not declare a
    # meaningful least-privilege boundary. Granting the entire VFS root would
    # let an artifact turn its own string interpolation into a file oracle.
    # The Agent must place dynamic resources below a static subdirectory (for
    # example ``/data/images/${name}``).
    if is_dynamic and normalized in {"/data", "/memory", "/logs", "/mount", "/run"}:
        return None
    if is_prefix:
        normalized += "/"
    return normalized


def html_vfs_read_rules(html: str) -> tuple[str, ...]:
    """Return exact files or static directory prefixes referenced by HTML.

    The Agent keeps using ordinary Linux paths; no capability vocabulary is
    added to the tool schema. Dynamic template strings are narrowed to their
    longest static directory prefix.
    """
    parser = _ResourceAttributeParser()
    try:
        parser.feed(html)
    except Exception:
        # Quoted JS/CSS scanning below remains useful for malformed fragments.
        pass
    candidates = list(parser.paths)
    candidates.extend(
        match.group("path") for match in _QUOTED_LOCAL_PATH.finditer(html)
    )
    candidates.extend(match.group("path") for match in _CSS_URL.finditer(html))
    rules = {
        rule
        for candidate in candidates
        if (rule := _rule_from_candidate(candidate)) is not None
    }
    return tuple(sorted(rules))


def rules_for_root(rules: tuple[str, ...], root: str) -> tuple[str, ...]:
    prefix = "/" + root.strip("/") + "/"
    return tuple(rule for rule in rules if rule.startswith(prefix))
