"""Chat and execution routes call ``run_agent_turn`` without a thread bridge."""
import inspect

from vibecanvas_api import agent
from vibecanvas_api.routes import chats, executions


def test_thread_bridge_symbols_deleted_from_agent():
    for sym in ("run_sync_agent_in_thread", "run_turn", "stream_buffer_as_sse"):
        assert not hasattr(agent, sym), f"agent.{sym} should be deleted"


def test_chats_route_does_not_reference_thread_bridge():
    src = inspect.getsource(chats)
    for sym in ("run_sync_agent_in_thread", "stream_buffer_as_sse"):
        assert sym not in src, f"{sym} lingers in chats.py"


def test_executions_route_does_not_reference_thread_bridge():
    src = inspect.getsource(executions)
    for sym in ("run_sync_agent_in_thread", "stream_buffer_as_sse"):
        assert sym not in src, f"{sym} lingers in executions.py"
