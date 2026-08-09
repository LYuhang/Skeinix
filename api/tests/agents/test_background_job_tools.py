from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from vibecanvas_api.agents.tools import build_tools
from vibecanvas_api.agents.tools.background.background_jobs import (
    _do_background_job_cancel,
    _do_background_job_get,
    _do_background_job_list,
)
from vibecanvas_api.agents.tools.subagent.toolset import (
    build_agent_subagent_tools,
)
from vibecanvas_api.services.agent_runtime.approval import (
    is_pre_tool_approval_candidate,
)
from vibecanvas_api.storage.background_jobs_repo import (
    _output_envelope,
    _sanitize_public_output,
)


@pytest.mark.asyncio
async def test_main_agent_lists_background_jobs_through_control_plane():
    controller = AsyncMock(
        return_value={
            "action": "accepted",
            "payload": {
                "jobs": [
                    {
                        "job_id": "job_1",
                        "title": "Inspect files",
                        "status": "running",
                    }
                ]
            },
        }
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(background_job_submitter=controller),
        tool_call_id="call_list",
    )

    content, artifact = await _do_background_job_list(False, 25, None, runtime)

    controller.assert_awaited_once_with(
        "call_list",
        {"operation": "list", "include_finished": False, "limit": 25, "cursor": None},
    )
    assert "job_1" in content
    assert artifact["status"] == "success"


@pytest.mark.asyncio
async def test_main_agent_cancels_background_job_through_control_plane():
    controller = AsyncMock(
        return_value={
            "action": "accepted",
            "job_id": "job_1",
            "payload": {
                "job": {
                    "job_id": "job_1",
                    "title": "Inspect files",
                    "status": "cancelling",
                }
            },
        }
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(background_job_submitter=controller),
        tool_call_id="call_cancel",
    )

    content, artifact = await _do_background_job_cancel("job_1", runtime)

    controller.assert_awaited_once_with(
        "call_cancel",
        {"operation": "cancel", "job_id": "job_1"},
    )
    assert "cancelling" in content
    assert artifact["status"] == "success"


@pytest.mark.asyncio
async def test_main_agent_gets_background_job_status_and_result():
    controller = AsyncMock(
        return_value={
            "action": "accepted",
            "job_id": "job_1",
            "payload": {
                "job": {
                    "job_id": "job_1",
                    "title": "Inspect files",
                    "status": "completed",
                    "progress": {"current": 1, "total": 1, "message": "Done"},
                    "result": {"summary": "Found 3 files"},
                    "result_ref": "/memory/jobs/job_1/result.json",
                    "error": {},
                }
            },
        }
    )
    runtime = SimpleNamespace(
        context=SimpleNamespace(background_job_submitter=controller),
        tool_call_id="call_get",
    )

    content, artifact = await _do_background_job_get(" job_1 ", runtime)

    controller.assert_awaited_once_with(
        "call_get",
        {"operation": "get", "job_id": "job_1"},
    )
    assert "completed" in content
    assert "Found 3 files" in content
    assert "/memory/jobs/job_1/result.json" in content
    assert artifact["status"] == "success"


def test_background_controls_are_main_agent_only_and_do_not_request_hitl():
    main_names = {tool.name for tool in build_tools(set())}
    worker_names = {tool.name for tool in build_agent_subagent_tools()}

    assert {
        "background_job_list",
        "background_job_get",
        "background_job_cancel",
    } <= main_names
    assert {
        "background_job_list",
        "background_job_get",
        "background_job_cancel",
    }.isdisjoint(
        worker_names
    )
    assert not is_pre_tool_approval_candidate("background_job_list", {})
    assert not is_pre_tool_approval_candidate(
        "background_job_cancel", {"job_id": "job_1"}
    )
    assert not is_pre_tool_approval_candidate(
        "background_job_get", {"job_id": "job_1"}
    )


def test_background_output_envelope_is_bounded_and_redacts_secret_fields():
    result = _sanitize_public_output({
        "summary": "done",
        "token": "private",
        "nested": {"password": "private"},
    })
    envelope = _output_envelope(
        status="completed", result=result, result_ref="/memory/result.json", error={},
    )
    assert envelope == {
        "state": "final",
        "inline": {
            "summary": "done",
            "token": "[redacted]",
            "nested": {"password": "[redacted]"},
        },
        "summary": "done",
        "ref": "/memory/result.json",
        "truncated": False,
    }
    large = _output_envelope(
        status="completed", result={"data": "x" * 13_000},
        result_ref="/memory/large.json", error={},
    )
    assert large["inline"] is None and large["truncated"] is True
