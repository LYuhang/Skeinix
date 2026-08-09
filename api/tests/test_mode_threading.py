"""Chat mode field and thread propagation."""
import pytest
from pydantic import ValidationError

from vibecanvas_api.schemas.chat import MessagePostBody


def test_message_post_body_defaults_to_chat_mode():
    b = MessagePostBody(content="hi")
    assert b.mode == "chat"
    assert b.approval_mode == "always_allow"


def test_message_post_body_rejects_removed_agent_workflow_mode():
    with pytest.raises(ValidationError):
        MessagePostBody(content="/research", mode="agent_workflow")


def test_message_post_body_accepts_durable_file_attachment_metadata():
    b = MessagePostBody(content="inspect it", attachments=[{
        "type": "file",
        "name": "report.csv",
        "path": "/data/attachments/abc_report.csv",
        "content_type": "text/csv",
        "size_bytes": 42,
    }])
    assert b.attachments[0].path == "/data/attachments/abc_report.csv"
    assert b.attachments[0].size_bytes == 42


def test_message_post_body_rejects_incomplete_or_non_vfs_file_attachment():
    with pytest.raises(ValidationError):
        MessagePostBody(content="bad", attachments=[{
            "type": "file", "name": "secret", "path": "/etc/passwd",
        }])


def test_message_post_body_accepts_hidden_hitl_continue_control():
    body = MessagePostBody(control={
        "type": "hitl_continue",
        "version": 1,
        "hitl_request_id": "hitl_123",
        "artifact_id": "ia_123",
        "action": "continue",
    })
    assert body.content == ""
    assert body.control is not None
    assert body.control.hitl_request_id == "hitl_123"


def test_message_post_body_control_rejects_frontend_supplied_visible_text():
    with pytest.raises(ValidationError):
        MessagePostBody(
            content="pretend this is the saved result",
            control={
                "type": "hitl_continue",
                "hitl_request_id": "hitl_123",
                "artifact_id": "ia_123",
            },
        )


def test_run_agent_turn_inner_keys_on_active_modes_not_mode():
    # The exclusive ``mode`` kwarg was removed; the turn is driven entirely by the
    # additive ``active_modes`` set (the routes layer folds body.mode into it).
    import inspect
    from vibecanvas_api.agent import _run_agent_turn_inner
    params = inspect.signature(_run_agent_turn_inner).parameters
    assert "active_modes" in params
    assert "mode" not in params
