# -*- coding: utf-8 -*-
"""SerialToolExecutionMiddleware (FU-2) — tool calls execute one at a time."""
import asyncio

import pytest

from vibecanvas_api.agents.middleware.serial_tools import SerialToolExecutionMiddleware


@pytest.mark.asyncio
async def test_tool_calls_run_serially_not_interleaved():
    mw = SerialToolExecutionMiddleware()
    order = []

    async def handler(req):
        order.append(("start", req))
        await asyncio.sleep(0.02)   # would interleave if run in parallel
        order.append(("end", req))
        return f"result_{req}"

    # Simulate ToolNode's asyncio.gather over 3 tool_calls.
    results = await asyncio.gather(*[mw.awrap_tool_call(i, handler) for i in range(3)])

    assert results == ["result_0", "result_1", "result_2"]
    # Serial → every start is immediately followed by its own end (no interleave).
    for i in range(0, len(order), 2):
        assert order[i][0] == "start"
        assert order[i + 1] == ("end", order[i][1])


@pytest.mark.asyncio
async def test_passes_through_result_and_request():
    mw = SerialToolExecutionMiddleware()

    async def handler(req):
        return {"echo": req}

    assert await mw.awrap_tool_call("X", handler) == {"echo": "X"}
