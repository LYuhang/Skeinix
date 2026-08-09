# -*- coding: utf-8 -*-
"""Pure, DB-free foundation for the content-addressed library overlay.

Parses a ``requirements.txt``-format declaration into install specs that KEEP
the version specifier, and derives a stable content-address key from them.

This parser feeds the overlay BUILDER, which must install the EXACT pinned
versions the user declared (so the version specifier is preserved, unlike a
bare-module-name parse).
"""

import hashlib
import re

# Splits a requirement line at the first specifier / extras / marker / sep char,
# yielding (distribution_name, remainder). We KEEP the specifier portion of the
# remainder (the overlay builder installs exact pinned versions).
_NAME_SPLIT = re.compile(r"[<>=!~;,\[\] ]")

# A version specifier clause: one of the comparison operators followed by a
# version token. We rebuild the spec from these so extras/markers are dropped
# but the version constraint survives.
_SPEC_CLAUSE = re.compile(r"(==|>=|<=|~=|!=|<|>)\s*([^,;\[\]\s]+)")

# The ``::pypi`` suffix reserves room for a future index dimension; v1 is public
# PyPI only, so the suffix is constant.
_INDEX_SUFFIX = "::pypi"


def parse_install_specs(text: str | None) -> list[str]:
    """Parse ``requirements.txt`` text into sorted, deduped install specs.

    KEEPS the version specifier (the overlay builder installs exact versions):

      * Split on lines; strip inline ``#`` comments; skip blank lines and pip
        option lines (start with ``-`` / ``--``, e.g. ``-r``, ``--index-url``).
      * Strip environment markers (``; python_version<'3.9'``) and extras
        (``pkg[extra]`` → ``pkg`` + version). KEEP the version specifier
        (``==``/``>=``/``~=``/``!=``/``<``/``>`` + version).
      * Lowercase the distribution name (PyPI is case-insensitive on names);
        keep the specifier as-is.
      * Dedup case-insensitively by the full normalized spec; SORT the result.
      * Malformed line → skipped, never raised.
    """
    if not text:
        return []
    specs: set[str] = set()
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue  # blank / comment / pip option line (-r, --index-url, …)
        # Drop the marker portion (everything after ';') before reading specs.
        head = line.split(";", 1)[0].strip()
        if not head:
            continue
        # Distribution name ends at the first specifier / extras / marker char.
        name = _NAME_SPLIT.split(head, maxsplit=1)[0].strip().lower()
        if not name:
            continue
        # Rebuild the version specifier from any comparison clauses, dropping
        # extras like ``[security]``. Joined with ',' to match pip's syntax.
        clauses = [op + ver for op, ver in _SPEC_CLAUSE.findall(head)]
        specs.add(name + ",".join(clauses))
    return sorted(specs)


def compute_overlay_key(text: str | None) -> str:
    """Stable 64-char hex content-address for an overlay declaration.

    Order-insensitive (parse sorts) and version-sensitive. The ``::pypi`` suffix
    reserves a future index dimension; v1 hashes only the public-PyPI specs.
    """
    body = "\n".join(parse_install_specs(text)) + "\n" + _INDEX_SUFFIX
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
