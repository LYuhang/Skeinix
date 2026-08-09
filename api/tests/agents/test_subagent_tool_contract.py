from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from vibecanvas_api.agent import AgentContext
from vibecanvas_api.agents.middleware.user_approval import UserApprovalMiddleware
from vibecanvas_api.agents.tools.subagent.core import (
    SubAgentResult,
    _subagent_context_middleware,
)
from vibecanvas_api.agents.tools.subagent.subagent import _do_subagent, subagent
from vibecanvas_api.agents.tools.subagent.toolset import (
    SUBAGENT_DEFAULT_TOOL_NAMES,
    SUBAGENT_FORBIDDEN_HITL_TOOL_NAMES,
    build_agent_subagent_tools,
)
from vibecanvas_api.services.agent_runtime.approval import PRE_TOOL_APPROVAL_TOOLS


def test_subagent_default_toolset_is_fixed_and_hitl_free():
    names = tuple(tool.name for tool in build_agent_subagent_tools())

    assert names == SUBAGENT_DEFAULT_TOOL_NAMES == (
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "read_images",
        "web_search",
        "bash",
    )
    assert not set(names) & PRE_TOOL_APPROVAL_TOOLS
    assert not set(names) & SUBAGENT_FORBIDDEN_HITL_TOOL_NAMES
    assert "render_interactive" not in names
    assert "set_output" not in names

    middleware = _subagent_context_middleware(AgentContext())
    assert not any(isinstance(item, UserApprovalMiddleware) for item in middleware)


def test_subagent_schema_exposes_explicit_sync_or_background_execution():
    schema = subagent.tool_call_schema.model_json_schema()

    assert set(schema["properties"]) == {
        "title",
        "prompt",
        "max_iterations",
        "background_run",
    }
    assert "task" not in schema["properties"]
    assert "system_prompt" not in schema["properties"]
    assert "concise sentence" in schema["properties"]["title"]["description"]
    assert "no parent history" in schema["properties"]["prompt"]["description"]
    assert "action mode" in schema["properties"]["prompt"]["description"]
    assert schema["properties"]["background_run"]["default"] is False
    assert "durable background job" in schema["properties"]["background_run"]["description"]
    assert "prompt`` argument is the only task context" in subagent.description
    assert "Bad example" in subagent.description


@pytest.mark.asyncio
async def test_subagent_routes_self_contained_prompt_as_worker_input_without_replacing_system_prompt():
    worker_result = SubAgentResult(
        status="done",
        output={"result": "verified"},
        trace=[],
    )
    runtime = SimpleNamespace(
        context=AgentContext(
            chat_id="chat_subagent_contract",
            tenant_id="00000000-0000-0000-0000-000000000001",
        ),
        tool_call_id="tc_subagent_contract",
    )
    prompt = (
        "Inspect /workspace/api/auth.py for the reported refresh-token race. "
        "Do not edit code. Return file/line evidence, root cause, and the exact "
        "focused test command used to verify the conclusion."
    )

    with patch(
        "vibecanvas_api.agent._build_chat_model",
        return_value=object(),
    ), patch(
        "vibecanvas_api.agents.tools.subagent.toolset.build_agent_subagent_tools",
        return_value=[],
    ), patch(
        "vibecanvas_api.agents.tools.subagent.core.run_bounded_agent",
        new=AsyncMock(return_value=worker_result),
    ) as run_mock:
        content, artifact = await _do_subagent(
            "Audit refresh-token race",
            prompt,
            12,
            runtime,
        )

    kwargs = run_mock.call_args.kwargs
    assert kwargs["system_prompt"] != prompt
    assert "no parent conversation history" in kwargs["system_prompt"]
    assert kwargs["user_input"] == (
        "# Delegated task\n"
        "Title: Audit refresh-token race\n\n"
        "## Complete task packet\n"
        f"{prompt}"
    )
    assert kwargs["max_iterations"] == 12
    assert "verified" in content
    assert artifact["status"] == "success"


@pytest.mark.asyncio
async def test_subagent_rejects_a_missing_task_packet_before_starting_worker():
    runtime = SimpleNamespace(
        context=AgentContext(chat_id="chat_subagent_invalid"),
        tool_call_id="tc_subagent_invalid",
    )

    content, artifact = await _do_subagent(
        "Review authentication",
        "",
        25,
        runtime,
    )

    assert "complete, self-contained task packet" in content
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "invalid_subagent_input"
    assert artifact["error"]["info"] == {"field": "prompt"}


@pytest.mark.asyncio
async def test_background_subagent_submits_durable_job_without_running_inline():
    submitter = AsyncMock(
        return_value={"action": "accepted", "job_id": "job_contract"}
    )
    runtime = SimpleNamespace(
        context=AgentContext(
            chat_id="chat_subagent_background",
            tenant_id="00000000-0000-0000-0000-000000000001",
            background_job_submitter=submitter,
        ),
        tool_call_id="tc_subagent_background",
    )

    with patch(
        "vibecanvas_api.agents.tools.subagent.core.run_bounded_agent",
        new=AsyncMock(),
    ) as run_mock:
        content, artifact = await _do_subagent(
            "Inspect independent files",
            "Read /mount/data/a.json and return a concise schema report.",
            17,
            runtime,
            True,
        )

    run_mock.assert_not_awaited()
    submitter.assert_awaited_once_with(
        "tc_subagent_background",
        {
            "executor_type": "langchain_subagent",
            "tool_name": "subagent",
            "title": "Inspect independent files",
            "input": {
                "title": "Inspect independent files",
                "prompt": (
                    "Read /mount/data/a.json and return a concise schema report."
                ),
                "max_iterations": 17,
            },
        },
    )
    assert "job_contract" in content
    assert "status: queued" in content
    assert artifact["status"] == "success"
    assert artifact["artifact"]["handles"]["job_id"] == "job_contract"
