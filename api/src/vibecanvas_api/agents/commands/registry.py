"""Persistent built-in command registry.

Commands are platform-owned capabilities activated by slash commands such as
``/build``. Once activated, a command stays active for the chat: tools are gated
by the active command set, and command context is injected next to the latest
activation message by ``CommandContextEdit``.
"""

from __future__ import annotations

from dataclasses import dataclass

from vibecanvas_api.agents.prompts.browser import BROWSER
from vibecanvas_api.agents.prompts.build import BUILD
from vibecanvas_api.agents.prompts.deployment import DEPLOYMENT
from vibecanvas_api.agents.prompts.knowledge import KNOWLEDGE
from vibecanvas_api.agents.prompts.plan import PLAN
from vibecanvas_api.agents.prompts.task import TASK
from vibecanvas_api.agents.prompts.diagram import DIAGRAM
from vibecanvas_api.browser.playwright_contract import PLAYWRIGHT_AGENT_TOOLS


COMMAND_CONTEXT_HEADER = "<command-context"

@dataclass(frozen=True)
class CommandMode:
    name: str
    trigger: str
    kind: str
    sticky: bool
    context_prompt: str
    tools: list[str]
    activation_message: str | None
    external_control: dict | None
    sidepanel_only: bool = False


COMMAND_MODES: dict[str, CommandMode] = {
    "plan": CommandMode(
        name="plan",
        trigger="/plan",
        kind="additive",
        sticky=True,
        context_prompt=PLAN,
        tools=["create_execution_plan"],
        activation_message=(
            "This is the latest /plan activation. Follow the Dynamic Execution "
            "Plan contract above, not Workflow node definitions. Decide whether "
            "a durable graph adds value; if so, author and submit one strict Plan."
        ),
        external_control=None,
    ),
    "task": CommandMode(
        name="task",
        trigger="/task",
        kind="additive",
        sticky=True,
        context_prompt=TASK,
        tools=[
            "task_list",
            "task_get",
            "task_create_scheduled_run",
            "task_update_scheduled_run",
            "task_delete_scheduled_run",
            "task_cancel",
            "task_resume",
        ],
        activation_message=(
            "This is the latest /task activation. Use Task Center only for "
            "the durable task work requested in this message."
        ),
        external_control=None,
    ),
    "deployment": CommandMode(
        name="deployment",
        trigger="/deployment",
        kind="additive",
        sticky=True,
        context_prompt=DEPLOYMENT,
        tools=[
            "deployment_list",
            "deployment_get",
            "deployment_create",
            "deployment_update",
            "deployment_delete",
        ],
        activation_message=(
            "This is the latest /deployment activation. Work only with the "
            "deployment resources needed by the user's request."
        ),
        external_control=None,
    ),
    "knowledge": CommandMode(
        name="knowledge",
        trigger="/knowledge",
        kind="additive",
        sticky=True,
        context_prompt=KNOWLEDGE,
        tools=[
            "list_knowledge_bases",
            "get_knowledge_base",
            "list_knowledge_files",
            "search_knowledge",
            "read_knowledge_file",
        ],
        activation_message=(
            "This is the latest /knowledge activation. Discover authorized "
            "knowledge bases before searching them."
        ),
        external_control=None,
    ),
    "build": CommandMode(
        name="build",
        trigger="/build",
        kind="additive",
        sticky=True,
        context_prompt=BUILD,
        tools=[
            "list_workflows",
            "set_workflow",
            "create_workflow",
            "run_workflow",
            "node_execute",
            "batch_execute",
            "get_node_spec",
            "get_workflow",
            "check_workflow",
            "update_canvas",
            "new_version",
        ],
        activation_message=(
            "This is the latest /build activation. Treat the user's message as "
            "workflow construction or optimization work. If the user has not said "
            "what to build, ask what to build."
        ),
        external_control=None,
    ),
    "browser": CommandMode(
        name="browser",
        trigger="/browser",
        kind="additive",
        sticky=True,
        context_prompt=BROWSER,
        tools=list(PLAYWRIGHT_AGENT_TOOLS),
        activation_message=(
            "This is the latest /browser activation. The side-panel send action "
            "has already requested control of the visible page. Capture a fresh "
            "Playwright snapshot before choosing the first action."
        ),
        external_control=None,
        sidepanel_only=True,
    ),
    "diagram": CommandMode(
        name="diagram",
        trigger="/diagram",
        kind="additive",
        sticky=True,
        context_prompt=DIAGRAM,
        tools=[
            "get_diagram_spec",
            "search_diagram_assets",
            "inspect_diagram",
            "check_diagram",
            "render_interactive",
            "review_diagram",
            "export_diagram",
        ],
        activation_message=(
            "This is the latest /diagram activation. Use the enabled Registry "
            "catalog and complete the auto-save -> check -> render -> review chain. "
            "A review edit_source action must be repaired and reviewed again; "
            "do not stop until the latest review action is deliver or the bounded "
            "review limit is reached."
        ),
        external_control=None,
    ),
}


_TRIGGER_TO_NAME: dict[str, str] = {m.trigger: name for name, m in COMMAND_MODES.items()}


def parse_command(content: str) -> tuple[str | None, str]:
    """Resolve a leading slash command and return ``(name, stripped_content)``."""
    if not content:
        return None, content
    stripped_left = content.lstrip()
    if not stripped_left.startswith("/"):
        return None, content
    parts = stripped_left.split(None, 1)
    token = parts[0]
    name = _TRIGGER_TO_NAME.get(token)
    if name is None:
        return None, content
    return name, parts[1] if len(parts) > 1 else ""


def command_context_for(command: str) -> str:
    """Return the full persistent context injected for an active command."""
    cfg = COMMAND_MODES.get(command)
    if cfg is None:
        return ""
    parts = [
        f'{COMMAND_CONTEXT_HEADER} command="{cfg.name}">',
        cfg.context_prompt.strip(),
    ]
    if cfg.activation_message:
        parts.extend(["", "## Latest activation guidance", cfg.activation_message])
    parts.append("</command-context>")
    return "\n".join(parts)
