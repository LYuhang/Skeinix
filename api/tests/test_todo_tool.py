from types import SimpleNamespace

import pytest

from vibecanvas_api.agents.tools.todo.todo import _do_todo


def _runtime(items: list[dict] | None = None):
    return SimpleNamespace(context=SimpleNamespace(todo_items=list(items or [])))


def _todo_items(artifact: dict) -> list[dict]:
    return artifact["artifact"]["handles"]["todo_items"]


@pytest.mark.asyncio
async def test_todo_artifact_exposes_structured_items():
    runtime = _runtime()

    content, artifact = await _do_todo("add", "Sketch the workflow plan", 0, runtime)

    assert "[ ] 1. Sketch the workflow plan" in content
    assert runtime.context.todo_items == [
        {"id": 1, "text": "Sketch the workflow plan", "status": "pending"}
    ]
    assert _todo_items(artifact) == runtime.context.todo_items


@pytest.mark.asyncio
async def test_todo_done_clears_state_when_all_done():
    runtime = _runtime([
        {"id": 1, "text": "Sketch the workflow plan", "status": "in_progress"},
    ])

    content, artifact = await _do_todo("done", "", 1, runtime)

    assert content == "(empty)"
    assert runtime.context.todo_items == []
    assert _todo_items(artifact) == []


@pytest.mark.asyncio
async def test_todo_done_keeps_state_until_every_item_done():
    runtime = _runtime([
        {"id": 1, "text": "Sketch the workflow plan", "status": "in_progress"},
        {"id": 2, "text": "Apply canvas update", "status": "pending"},
    ])

    content, artifact = await _do_todo("done", "", 1, runtime)

    assert "[x] 1. Sketch the workflow plan" in content
    assert "[ ] 2. Apply canvas update" in content
    assert runtime.context.todo_items == [
        {"id": 1, "text": "Sketch the workflow plan", "status": "done"},
        {"id": 2, "text": "Apply canvas update", "status": "pending"},
    ]
    assert _todo_items(artifact) == runtime.context.todo_items
