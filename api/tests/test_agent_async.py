"""run_agent_turn is natively asynchronous without a thread bridge."""
import inspect

from vibecanvas_api import agent


def test_run_agent_turn_is_async_generator():
    assert inspect.isasyncgenfunction(agent.run_agent_turn), (
        f"run_agent_turn must be `async def` with `yield`; "
        f"got {type(agent.run_agent_turn)}"
    )
