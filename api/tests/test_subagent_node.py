from __future__ import annotations

from unittest.mock import AsyncMock, patch

from vibecanvas_api.services.sandbox.workflow_guard import (
    SANDBOX_RUNNABLE_NODE_TYPES,
    classify_workflow,
)
from vibecanvas_engine.nodes.base import BaseNode
from vibecanvas_engine.nodes.subagent import SubAgentNode
from vibecanvas_engine.register import node_registry
from vibecanvas_api.agents.tools.subagent.core import SubAgentResult


def _make_node(*, output_fields=None, node_config=None):
    return SubAgentNode(
        node_type="SubAgentNode",
        node_id="node_2",
        node_name="worker",
        node_description="a bounded sub-agent",
        input_fields={"task": {"type": "string", "value": "", "reference": ""}},
        output_fields=output_fields
        or {"answer": {"type": "string", "description": "the answer"}},
        node_config=node_config
        or {
            "task_template": "Summarize this task: {{task}}",
            "model_name": "m1",
            "max_iterations": 5,
        },
        children=[],
    )


def test_subagent_node_registered_and_in_schema():
    assert node_registry.get("SubAgentNode") is not None
    assert (
        "SubAgentNode"
        in BaseNode.GENERAL_NODE_SCHEMA["properties"]["node_type"]["enum"]
    )


def test_subagent_node_in_sandbox_allowlist():
    assert "SubAgentNode" in SANDBOX_RUNNABLE_NODE_TYPES


def test_subagent_node_populates_output_fields():
    node = _make_node()
    result = SubAgentResult(
        "done", {"answer": "ok"}, trace=[{"role": "ai", "text": "ok"}]
    )

    with patch(
        "vibecanvas_api.agent._build_chat_model",
        return_value=object(),
    ), patch(
        "vibecanvas_api.agents.tools.subagent.toolset.build_agent_subagent_tools",
        return_value=[],
    ), patch(
        "vibecanvas_api.agents.tools.subagent.core.run_bounded_agent",
        new=AsyncMock(return_value=result),
    ) as run_mock:
        envelope = node(
            {"task": "summarize"},
            {},
            extra={
                "run_id": "r1",
                "llm_credentials": {
                    "m1": {
                        "provider": "openai",
                        "model_name": "gpt-test",
                        "api_url": "https://broker.example.invalid/v1",
                        "api_key": "test-runtime-capability",
                    }
                },
            },
        )

    assert envelope["status"] == "success", envelope
    assert envelope["output"] == {"answer": "ok"}
    kwargs = run_mock.call_args.kwargs
    assert kwargs["user_input"] == "Summarize this task: summarize"
    assert "bounded workflow sub-agent" in kwargs["system_prompt"]


def test_subagent_node_uses_default_model_settings_from_credentials():
    node = _make_node()
    result = SubAgentResult("done", {"answer": ""}, trace=[])
    credential = {
        "provider": "openai",
        "model_name": "gpt-test",
        "api_key": "test-only-api-key",
        "api_url": "https://example.invalid/v1",
        "model_context_tokens": 128000,
    }

    with patch(
        "vibecanvas_api.agent._build_chat_model",
        return_value=object(),
    ) as model_mock, patch(
        "vibecanvas_api.agents.tools.subagent.toolset.build_agent_subagent_tools",
        return_value=[],
    ), patch(
        "vibecanvas_api.agents.tools.subagent.core.run_bounded_agent",
        new=AsyncMock(return_value=result),
    ):
        node(
            {"task": "summarize"},
            {},
            extra={"run_id": "r1", "llm_credentials": {"m1": credential}},
        )

    agent_cfg = model_mock.call_args.args[0]
    assert agent_cfg["model"] == "openai:gpt-test"
    assert "temperature" not in agent_cfg
    assert "max_tokens" not in agent_cfg


def _subagent_wf() -> dict:
    return {
        "__meta__": {
            "workflow_id": "wf_subagent",
            "workflow_name": "subagent_smoke",
            "workflow_version": 1,
            "workflow_subversion": 0,
        },
        "node_1": {
            "node_id": "node_1",
            "node_name": "__start__",
            "node_type": "StartNode",
            "node_description": "start",
            "input_fields": {},
            "output_fields": {"task": {"type": "string", "description": "the task"}},
            "node_config": {},
            "children": ["node_2"],
        },
        "node_2": {
            "node_id": "node_2",
            "node_name": "worker",
            "node_type": "SubAgentNode",
            "node_description": "a bounded sub-agent",
            "input_fields": {
                "task": {"type": "string", "value": "", "reference": "__start__.task"}
            },
            "output_fields": {
                "answer": {"type": "string", "description": "the answer"}
            },
            "node_config": {
                "task_template": "Answer this task: {{task}}",
                "model_name": "m1",
                "max_iterations": 5,
            },
            "children": ["node_3"],
        },
        "node_3": {
            "node_id": "node_3",
            "node_name": "__end__",
            "node_type": "EndNode",
            "node_description": "end",
            "input_fields": {
                "answer": {"type": "string", "value": "", "reference": "worker.answer"}
            },
            "output_fields": {
                "answer": {"type": "string", "description": "the answer"}
            },
            "node_config": {},
            "children": [],
        },
    }


def test_subagent_workflow_is_database_free():
    assert classify_workflow(_subagent_wf()) == "pure"
