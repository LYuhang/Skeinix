"""Generate the frontend renderer validator schema from the backend source."""
from __future__ import annotations

import json
from pathlib import Path

from vibecanvas_api.services.platform_mcp.interactive_tools.schema import interactive_view_json_schema


REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = (
    REPO_ROOT
    / "web/src/components/agent-sidebar/tool-render/interactive-view-schema.generated.json"
)


def main() -> None:
    schema = interactive_view_json_schema()
    OUTPUT.write_text(
        json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"generated {OUTPUT.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
