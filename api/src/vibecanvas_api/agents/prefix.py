"""Helpers for building the Context-prefix block prepended to user messages.

When a user message carries ``type=="context"`` attachments, the backend
serializes those attachments into a structured prefix and prepends it to
the user's typed text BEFORE handing off to the agent. The agent sees:

    [Attached Context — N items]

    ## 1. <label>
      - focus: <focus_string>
      - workflow: <workflow ref>
      - execution_context: <inline JSON if non-empty>

    ## 2. ...

    ---

    [User]
    <user typed text>

The system prompt is NOT touched by this — Context lives entirely in the
user message so prefix cache stays warm across threads.
"""

from __future__ import annotations

import json


def partition_attachments(attachments) -> tuple[list[dict], list[dict]]:
    """Split attachments into (context_attachments, file_attachments).

    Accepts None or [] gracefully.
    """
    contexts: list[dict] = []
    files: list[dict] = []
    for att in attachments or []:
        if att.get("type") == "context":
            contexts.append(att)
        else:
            files.append(att)
    return contexts, files


def build_context_prefix(contexts: list[dict]) -> str:
    """Serialize Context attachments into the user-message prefix block.

    Returns "" if no contexts. Otherwise returns a multi-line block ending
    with "\\n---\\n\\n[User]\\n" that the caller prepends to user content.
    """
    if not contexts:
        return ""

    lines: list[str] = []
    n = len(contexts)
    lines.append(f"[Attached Context — {n} item{'s' if n != 1 else ''}]")
    lines.append("")

    for idx, ctx in enumerate(contexts, start=1):
        label = ctx.get("label", "(unnamed)")
        focus = ctx.get("focus", "")
        ref = ctx.get("ref", "")
        exec_ctx = ctx.get("execution_context") or {}

        lines.append(f"## {idx}. {label}")
        lines.append(f"  - focus: {focus}")
        lines.append(f"  - workflow: {ref}")
        if exec_ctx:
            lines.append(f"  - execution_context: {json.dumps(exec_ctx, ensure_ascii=False)}")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("[User]")
    return "\n".join(lines) + "\n"


def build_file_attachment_prefix(files: list[dict]) -> str:
    """Expose durable uploaded-file paths to the model as ordinary Linux files.

    Bytes never enter the language-model payload.  The agent sees the same VFS
    paths its file tools can read, while ``additional_kwargs.attachments`` keeps
    the structured metadata available to history/resume consumers.
    """
    visible = [
        item for item in files
        if item.get("type") in {"file", "image", "video"} and item.get("path")
    ]
    if not visible:
        return ""
    lines = [f"[Attached Files — {len(visible)} item{'s' if len(visible) != 1 else ''}]", ""]
    for index, item in enumerate(visible, start=1):
        lines.extend([
            f"## {index}. {item.get('name') or item['path'].rsplit('/', 1)[-1]}",
            f"  - type: {item.get('type', 'file')}",
            f"  - path: {item['path']}",
        ])
        if item.get("content_type"):
            lines.append(f"  - content_type: {item['content_type']}")
        lines.append("")
    lines.extend(["---", "", "[User]"])
    return "\n".join(lines) + "\n"


_CONTEXT_HEADER = "[Attached Context"
_FILES_HEADER = "[Attached Files"
_USER_BOUNDARY = "\n[User]\n"
_STRIP_PLACEHOLDER_PREFIX = "[earlier context attachments stripped"


def strip_context_prefix(content):
    """Return the user-typed text with any context-prefix block removed.

    Handles two known shapes produced upstream:
      - the original "[Attached Context — N items] ... [User]\\n<text>"
        block produced by build_context_prefix
      - the post-strip "[earlier context attachments stripped …]\\n<text>"
        placeholder produced by ContextPrefixStripEdit

    Anything else is returned unchanged. Non-string inputs (e.g. multimodal
    content lists, None) are passed through unchanged so this stays safe to
    call on arbitrary HumanMessage.content values.
    """
    if not isinstance(content, str):
        return content
    if content.startswith((_CONTEXT_HEADER, _FILES_HEADER)):
        boundary = content.rfind(_USER_BOUNDARY)
        if boundary != -1:
            return content[boundary + len(_USER_BOUNDARY):]
        return content
    if content.startswith(_STRIP_PLACEHOLDER_PREFIX):
        nl = content.find("\n")
        if nl != -1:
            return content[nl + 1:]
        return content
    return content
