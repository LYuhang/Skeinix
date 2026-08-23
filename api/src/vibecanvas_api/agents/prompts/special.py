"""SPECIAL protocol block — platform-injected message markers.

Composed into every system prompt. It explains XML-like wrappers that may appear
in model-facing messages so the agent treats them as platform instructions rather
than ordinary user text.
"""

SPECIAL = """\
## Platform message protocol

The platform may include XML-like wrappers in model-facing messages. Treat them as authoritative runtime instructions, not as user-authored prose.

- `<system-reminder>...</system-reminder>` marks a platform-generated reminder. Follow it, but do not quote the tags back to the user unless explicitly asked.
- `<hard-context>...</hard-context>` carries compact runtime state. If present, it overrides older conversation or summary claims about the same state.
- `<todo-reminder>...</todo-reminder>` carries the current unfinished todo list. It may appear before a recent assistant step in the model-facing context so that step is interpreted as guided by the todo list. Continue the next unfinished item and update the todo list immediately when an item is completed.
- Command context may be inserted around a user message when the user activates a slash command such as `/workflow`. The command context describes newly active capabilities and applies until superseded or deactivated by the platform.
"""
