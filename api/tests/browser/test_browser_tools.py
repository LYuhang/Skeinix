import asyncio
import types
import pytest
from langgraph.prebuilt.tool_node import ToolRuntime
from vibecanvas_api.services.platform_mcp import browser_tools as bt
from vibecanvas_api.services.platform_mcp.browser_tools import _common
from vibecanvas_api.services.platform_mcp.browser_tools import session as browser_session
from vibecanvas_api.services.platform_mcp.browser_tools.read import _render_snapshot, _render_tab, _take_screenshot
from vibecanvas_api.services.platform_mcp.browser_tools.session import _render_start_session
from vibecanvas_api.agents.tools.decorator import ToolError


def FakeRuntime(context):
    """Build a real ToolRuntime carrying the test context (LangChain validates the
    injected `runtime` arg against the ToolRuntime dataclass type)."""
    return ToolRuntime(state={}, context=context, config={}, stream_writer=None,
                       tool_call_id="t", store=None)


class FakeAgentBrowser:
    def __init__(self): self.calls = []

    async def navigate(self, **kw):
        self.calls.append(("navigate", kw))
        return {"ok": True, "data": {"final_url": kw["url"], "title": "T"}, "media": []}

    async def click(self, **kw):
        self.calls.append(("click", kw))
        return {"ok": True, "data": {}, "media": []}

    async def submit(self, **kw):
        self.calls.append(("submit", kw))
        return {"ok": True, "data": {}, "media": []}

    async def screenshot(self, **kw):
        self.calls.append(("screenshot", kw))
        return {"ok": True, "data": {}, "media": [
            {"slot": "screenshot", "path": "/data/browser-media/ab.png",
             "content_type": "image/png"}]}

    async def read_fields(self, **kw):
        self.calls.append(("read_fields", kw))
        return {"ok": True, "data": {"fields": {"price": "9.99"}}, "media": []}

    async def get_image(self, **kw):
        self.calls.append(("get_image", kw))
        return {"ok": True, "data": {}, "media": [
            {"slot": "image", "path": "/data/browser-media/el.png",
             "content_type": "image/png"}]}

    async def switch_tab(self, **kw):
        self.calls.append(("switch_tab", kw))
        return {"ok": True, "data": {}, "media": []}

    async def close_tab(self, **kw):
        self.calls.append(("close_tab", kw))
        return {"ok": True, "data": {}, "media": []}

    async def wait_for_new_tab(self, **kw):
        self.calls.append(("wait_for_new_tab", kw))
        return {"ok": True, "data": {"target_id": "t2"}, "media": []}

    async def list_open_tabs(self, **kw):
        self.calls.append(("list_open_tabs", kw))
        return {
            "ok": True,
            "data": {
                "tabs": [{"tab": 42, "title": "Example", "url": "https://x.test", "active": True}],
                "health": {
                    "state": "stale_extension_attachment",
                    "stale_attachment_count": 1,
                    "conflict_count": 0,
                    "missing_attachment_count": 0,
                    "recommended_action": "rediscover_tabs_and_start_selected_session",
                },
            },
            "media": [],
        }

    async def list_tabs(self, **kw):
        self.calls.append(("list_tabs", kw))
        return {
            "ok": True,
            "data": {
                "tabs": [{"tab": 7, "title": "Controlled", "url": "https://controlled.test", "active": True}],
                "controlled": True,
                "health": {
                    "state": "healthy",
                    "stale_attachment_count": 0,
                    "conflict_count": 0,
                    "missing_attachment_count": 0,
                    "recommended_action": "continue",
                },
            },
            "media": [],
        }

    async def fetch_resource(self, **kw):
        self.calls.append(("fetch_resource", kw))
        return {
            "ok": True,
            "data": {
                "resource": kw.get("save_path") or "/data/browser-media/resource.png",
                "resource_type": "image",
                "mime": "image/png",
            },
            "media": [
                {
                    "slot": "resource",
                    "path": kw.get("save_path") or "/data/browser-media/resource.png",
                    "bytes_len": 3,
                    "mime": "image/png",
                }
            ],
        }

    async def start_session(self, **kw):
        self.calls.append(("start_session", kw))
        return {"ok": True, "data": {"tab": kw.get("tab") or 1, "started": True}, "media": []}

    async def end_session(self, **kw):
        self.calls.append(("end_session", kw))
        return {"ok": True, "data": {"released": True}, "media": []}


class FakeSendAgentBrowser:
    def __init__(self): self.calls = []

    async def _send(self, cmd, **kw):
        self.calls.append((str(cmd), kw))
        return {"ok": True, "data": {}, "media": []}


@pytest.fixture(autouse=True)
def patch_builder(monkeypatch):
    fake = FakeAgentBrowser()
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: fake)

    async def active_binding(_ctx):
        return {"status": "attached"}

    monkeypatch.setattr(_common, "_load_browser_binding", active_binding)
    monkeypatch.setattr(browser_session, "_load_browser_binding", active_binding)

    async def reserve_binding(_ctx, browser_session_id):
        return {
            "status": "pending",
            "browser_session_id": browser_session_id,
            "browser_session_generation": 1,
        }

    async def confirm_binding(_ctx, **kwargs):
        return {
            "status": "attached",
            "browser_session_id": kwargs["browser_session_id"],
            "browser_session_generation": kwargs["browser_session_generation"],
        }

    async def persist_released(_ctx, _reason, **_kwargs):
        return None

    monkeypatch.setattr(browser_session, "_reserve_browser_session", reserve_binding)
    monkeypatch.setattr(browser_session, "_confirm_browser_session_attached", confirm_binding)
    monkeypatch.setattr(browser_session, "_persist_browser_released", persist_released)
    return fake


def _tool(name):
    return next(t for t in bt.BROWSER_TOOLS if t.name == name)


@pytest.mark.asyncio
async def test_session_status_reports_durable_state_and_model_reusable_tab_ids(patch_builder):
    ctx = types.SimpleNamespace(
        browser=object(), tenant_id="tenant_1", chat_id="c1", turn_id="turn_1",
    )
    out = await _tool("browser_session_status").ainvoke({
        "require_user_auth": False,
        "runtime": FakeRuntime(ctx),
    })
    assert "Browser control status: attached" in out
    assert "Extension connection: connected" in out
    assert "[7] 'Controlled'" in out
    assert "Browser health: healthy" in out
    assert "Recommended action: continue" in out
    assert patch_builder.calls[0][0] == "list_tabs"


@pytest.mark.asyncio
async def test_inactive_session_status_still_reports_extension_ghost_health(
    patch_builder, monkeypatch,
):
    async def inactive_binding(_ctx):
        return {"status": "inactive"}

    monkeypatch.setattr(browser_session, "_load_browser_binding", inactive_binding)
    ctx = types.SimpleNamespace(
        browser=object(), tenant_id="tenant_1", chat_id="c1", turn_id="turn_1",
    )

    out = await _tool("browser_session_status").ainvoke({
        "require_user_auth": False,
        "runtime": FakeRuntime(ctx),
    })

    assert "Browser control status: inactive" in out
    assert "Controlled tabs: none" in out
    assert "Browser health: stale_extension_attachment" in out
    assert patch_builder.calls[0][0] == "list_open_tabs"


def test_session_status_requires_auth_by_default():
    schema = _tool("browser_session_status").tool_call_schema.model_json_schema()
    assert schema["properties"]["require_user_auth"]["default"] is True


def test_session_status_auth_parameter_is_enforced_by_pre_tool_gate():
    from vibecanvas_api.agents.middleware.user_approval import (
        PRE_APPROVAL_TOOLS,
        requires_user_approval,
    )

    assert requires_user_approval("browser_session_status", {}, "agent") is True
    assert requires_user_approval(
        "browser_session_status", {"require_user_auth": False}, "agent"
    ) is False
    tools_by_name = {tool.name: tool for tool in bt.BROWSER_TOOLS}
    browser_approval_tools = {
        name for name in PRE_APPROVAL_TOOLS if name.startswith("browser_")
    }
    assert browser_approval_tools <= tools_by_name.keys()
    for tool_name in browser_approval_tools:
        schema = tools_by_name[tool_name].tool_call_schema.model_json_schema()
        assert schema["properties"]["require_user_auth"]["default"] is True
        assert schema["properties"]["approval_reason"]["default"] == ""


@pytest.mark.asyncio
async def test_browser_binding_requires_durable_chat_context(monkeypatch):
    # Exercise the real loader rather than the autouse fixture replacement.
    monkeypatch.undo()
    with pytest.raises(ToolError) as caught:
        await _common._load_browser_binding(types.SimpleNamespace())

    assert str(caught.value) == "browser_context_missing"
    assert caught.value.info["not_executed"] is True


@pytest.mark.asyncio
async def test_navigate_tool_returns_ok_envelope(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_navigate").ainvoke(
        {"url": "https://x.test", "runtime": FakeRuntime(ctx)})
    assert "Navigated to 'T' (https://x.test)" == out
    assert patch_builder.calls[0][0] == "navigate"


@pytest.mark.asyncio
async def test_screenshot_tool_returns_path_not_bytes(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    content, artifact = await _take_screenshot("", "", False, 0, FakeRuntime(ctx))
    assert content.endswith("/data/browser-media/ab.png")
    media = artifact["artifact"]["auxiliary"][0]
    assert media["path"].endswith(".png") and "bytes" not in media and "b64" not in media


@pytest.mark.asyncio
async def test_act_forwards_purpose_and_expect(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    await _tool("browser_click").ainvoke(
        {"handle": "h1", "purpose": "finish", "expect": "toast",
         "runtime": FakeRuntime(ctx)})
    cmd, kw = patch_builder.calls[0]
    assert cmd == "click" and kw["purpose"] == "finish" and kw["expect"] == "toast"


@pytest.mark.asyncio
async def test_no_transport_soft_errors(monkeypatch):
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: None)
    ctx = types.SimpleNamespace(browser=None, vfs_run=None, chat_id="c1")
    out = await _tool("browser_click").ainvoke(
        {"handle": "h1", "runtime": FakeRuntime(ctx)})
    # No transport → a tool error that TELLS the user where browser control lives.
    assert "side panel" in out.lower()


@pytest.mark.asyncio
async def test_browser_error_soft_errors(monkeypatch):
    class Boom:
        async def click(self, **kw):
            raise RuntimeError("ws gone")
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: Boom())
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_click").ainvoke(
        {"handle": "h1", "runtime": FakeRuntime(ctx)})
    # A raised exception surfaces in the error (not swallowed).
    assert "ws gone" in out


@pytest.mark.asyncio
async def test_common_run_injects_only_durable_session_and_turn_fencing(monkeypatch):
    fake = FakeSendAgentBrowser()
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: fake)

    async def fake_binding(_ctx):
        return {
            "status": "attached",
            "browser_session_id": "brs_1",
            "browser_session_generation": 7,
        }

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(
        browser=object(),
        tenant_id="tenant_1",
        chat_id="chat_1",
        turn_id="turn_1",
    )

    await _common._run("navigate", ctx, url="https://example.test")

    cmd, kw = fake.calls[0]
    assert cmd == "Cmd.NAVIGATE"
    assert kw["browser_session_id"] == "brs_1"
    assert kw["session_generation"] == 7
    assert "browser_window_id" not in kw
    assert "panel_context_id" not in kw
    assert kw["turn_id"] == "turn_1"
    assert kw["url"] == "https://example.test"


@pytest.mark.asyncio
async def test_common_run_timeout_after_send_reports_unknown_effect(monkeypatch):
    class TimeoutBrowser:
        async def _send(self, _cmd, **_kw):
            raise asyncio.TimeoutError()

    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: TimeoutBrowser())

    async def fake_binding(_ctx):
        return {"status": "attached"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="chat_1")

    with pytest.raises(ToolError) as caught:
        await _common._run("click", ctx, handle="h1")

    assert str(caught.value) == "browser_command_result_unknown"
    assert "effect_status=unknown" in (caught.value.message or "")
    assert "First read the target page" in (caught.value.message or "")


@pytest.mark.asyncio
async def test_common_run_observation_unknown_effect_preserved_for_agent(monkeypatch):
    class UnknownEffectBrowser:
        async def _send(self, _cmd, **_kw):
            return {
                "ok": False,
                "error": "command result was not confirmed",
                "effect_status": "unknown",
            }

    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: UnknownEffectBrowser())

    async def fake_binding(_ctx):
        return {"status": "attached"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="chat_1")

    with pytest.raises(ToolError) as caught:
        await _common._run("click", ctx, handle="h1")

    assert str(caught.value) == "browser_command_result_unknown"
    assert "command result was not confirmed" in (caught.value.message or "")
    assert "effect_status=unknown" in (caught.value.message or "")


@pytest.mark.asyncio
async def test_common_run_exposes_structured_debugger_conflict_health(monkeypatch):
    health = {
        "state": "external_debugger_conflict",
        "stale_attachment_count": 0,
        "conflict_count": 1,
        "missing_attachment_count": 0,
        "safe_to_cleanup": False,
        "recommended_action": "close_external_debugger_or_choose_another_tab",
    }

    class ConflictBrowser:
        async def _send(self, _cmd, **_kw):
            return {
                "ok": False,
                "data": {
                    "error_code": "browser_start_session_failed",
                    "error": "Another debugger is already attached to the tab",
                    "not_executed": True,
                    "error_info": {"not_executed": True, "health": health},
                },
            }

    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: ConflictBrowser())

    async def fake_binding(_ctx):
        return {"status": "attached"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="chat_1")

    with pytest.raises(ToolError) as caught:
        await _common._run("start_session", ctx, target="existing", tab=42)

    assert str(caught.value) == "browser_debugger_conflict"
    assert "Browser health: external_debugger_conflict" in (caught.value.message or "")
    assert "do not detach it automatically" in (caught.value.message or "")
    assert caught.value.info["health"]["safe_to_cleanup"] is False


@pytest.mark.asyncio
async def test_common_run_reads_unknown_effect_from_typed_observation_data(monkeypatch):
    class UnknownEffectBrowser:
        async def _send(self, _cmd, **_kw):
            return {
                "ok": False,
                "error": "handler failed after dispatch started",
                "data": {
                    "error_code": "browser_command_result_unknown",
                    "effect_status": "unknown",
                },
            }

    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: UnknownEffectBrowser())

    async def fake_binding(_ctx):
        return {"status": "attached"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="chat_1")

    with pytest.raises(ToolError) as caught:
        await _common._run("click", ctx, handle="h1")

    assert str(caught.value) == "browser_command_result_unknown"
    assert "effect_status=unknown" in (caught.value.message or "")


@pytest.mark.asyncio
async def test_common_run_released_session_rejects_before_execution(monkeypatch):
    fake = FakeSendAgentBrowser()
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: fake)

    async def fake_binding(_ctx):
        return {"status": "inactive"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="chat_1")

    with pytest.raises(ToolError) as caught:
        await _common._run("click", ctx, handle="h1")

    assert str(caught.value) == "browser_session_released"
    assert "operation was not executed" in (caught.value.message or "")
    assert fake.calls == []


@pytest.mark.asyncio
async def test_common_run_list_open_tabs_allowed_before_session(monkeypatch):
    fake = FakeSendAgentBrowser()
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: fake)

    async def fake_binding(_ctx):
        return {"status": "inactive"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(
        browser=object(),
        tenant_id="tenant_1",
        chat_id="chat_1",
        turn_id="turn_1",
    )

    await _common._run("list_open_tabs", ctx)

    cmd, kw = fake.calls[0]
    assert cmd == "Cmd.LIST_OPEN_TABS"
    assert kw["turn_id"] == "turn_1"
    assert "panel_context_id" not in kw
    assert "browser_window_id" not in kw


@pytest.mark.asyncio
async def test_wait_tool_no_browser_soft_errors(monkeypatch):
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: None)
    ctx = types.SimpleNamespace(browser=None, vfs_run=None, chat_id="c1")
    out = await _tool("browser_wait_for").ainvoke(
        {"selector": ".result", "runtime": FakeRuntime(ctx)})
    assert "side panel" in out.lower()


def test_wait_tool_present():
    names = {t.name for t in bt.BROWSER_TOOLS}
    assert "browser_wait_for" in names


def test_start_session_tool_present():
    names = {t.name for t in bt.BROWSER_TOOLS}
    assert "browser_start_session" in names


@pytest.mark.asyncio
async def test_start_session_tool_no_browser_reports_not_controllable(monkeypatch):
    # No browser transport (e.g. the main app): start_session is context-aware —
    # it returns a SUCCESS envelope with controlling=False + a user-relayable
    # message (NOT a generic transport error), so the agent can tell the user to
    # open the side panel.
    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: None)
    ctx = types.SimpleNamespace(browser=None, vfs_run=None, chat_id="c1")
    out = await _tool("browser_start_session").ainvoke(
        {"runtime": FakeRuntime(ctx)})
    assert "side panel" in out.lower()


@pytest.mark.asyncio
async def test_start_session_target_new_has_one_canonical_schema_and_wire_field(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_start_session").ainvoke(
        {"target": "new", "runtime": FakeRuntime(ctx)})
    assert "Browser session started." in out
    cmd, kw = patch_builder.calls[0]
    assert cmd == "start_session"
    assert kw["target"] == "new"
    assert "new_tab" not in kw
    assert "new_tab" not in _tool("browser_start_session").tool_call_schema.model_fields


@pytest.mark.asyncio
async def test_start_session_existing_forwards_tab_id(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_start_session").ainvoke(
        {"target": "existing", "tab": 42, "runtime": FakeRuntime(ctx)})
    assert "Controlling tab: 42" in out
    cmd, kw = patch_builder.calls[0]
    assert cmd == "start_session"
    assert kw["target"] == "existing"
    assert kw["tab"] == 42


@pytest.mark.asyncio
async def test_start_session_existing_requires_a_discovered_tab_id(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_start_session").ainvoke(
        {"target": "existing", "runtime": FakeRuntime(ctx)})

    assert "browser_tab(action='list_open')" in out
    assert patch_builder.calls == []


@pytest.mark.asyncio
async def test_start_session_rejects_tab_for_current_or_new_targets(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_start_session").ainvoke(
        {"target": "new", "tab": 42, "runtime": FakeRuntime(ctx)})

    assert "only valid when target='existing'" in out
    assert patch_builder.calls == []


@pytest.mark.asyncio
async def test_start_session_detaches_extension_when_durable_confirm_fails(
    patch_builder,
    monkeypatch,
):
    async def fail_confirm(_ctx, **_kwargs):
        raise ToolError(
            "browser_session_persist_failed",
            "Browser control attached, but saving its durable state failed.",
        )

    monkeypatch.setattr(browser_session, "_confirm_browser_session_attached", fail_confirm)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="c1")

    content, artifact = await browser_session._start_session(
        "current", 0, FakeRuntime(ctx),
    )

    assert [name for name, _args in patch_builder.calls] == ["start_session", "end_session"]
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "browser_session_persist_failed"
    assert "saving its durable state failed" in content


@pytest.mark.asyncio
async def test_model_visible_list_open_tab_id_drives_existing_session(patch_builder):
    """V1 discovery closure: the exact id rendered to the model is reusable."""
    ctx = types.SimpleNamespace(
        browser=object(),
        vfs_run=object(),
        chat_id="c1",
    )
    listed = await _tool("browser_tab").ainvoke(
        {"action": "list_open", "runtime": FakeRuntime(ctx)}
    )
    assert "[42] 'Example'" in listed

    await _tool("browser_start_session").ainvoke(
        {"target": "existing", "tab": 42, "runtime": FakeRuntime(ctx)}
    )
    cmd, args = patch_builder.calls[-1]
    assert cmd == "start_session"
    assert args["target"] == "existing"
    assert args["tab"] == 42
    assert "panel_context_id" not in args
    assert "browser_window_id" not in args


@pytest.mark.asyncio
async def test_take_screenshot_by_handle_routes_to_get_image(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_take_screenshot").ainvoke(
        {"handle": "h9", "runtime": FakeRuntime(ctx)})
    assert "Screenshot saved:" in out
    assert patch_builder.calls[0][0] == "get_image"
    assert patch_builder.calls[0][1]["handle"] == "h9"


@pytest.mark.asyncio
async def test_take_screenshot_no_handle_routes_to_screenshot(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    await _tool("browser_take_screenshot").ainvoke({"runtime": FakeRuntime(ctx)})
    assert patch_builder.calls[0][0] == "screenshot"


@pytest.mark.asyncio
@pytest.mark.parametrize("action,cmd", [
    ("switch", "switch_tab"), ("close", "close_tab"), ("wait_new", "wait_for_new_tab"),
    ("list_open", "list_open_tabs")])
async def test_browser_tab_routes_per_action(patch_builder, action, cmd):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_tab").ainvoke(
        {"action": action, "target_id": "t1", "runtime": FakeRuntime(ctx)})
    assert out
    assert patch_builder.calls[0][0] == cmd


@pytest.mark.asyncio
async def test_browser_tab_bad_action_soft_errors(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_tab").ainvoke(
        {"action": "nope", "runtime": FakeRuntime(ctx)})
    assert "unknown action 'nope'" in out
    assert patch_builder.calls == []  # never touched the browser


@pytest.mark.asyncio
async def test_fetch_resource_forwards_save_path(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_fetch_resource").ainvoke(
        {
            "url": "https://x.test/p.png",
            "save_path": "/data/downloads/p.png",
            "runtime": FakeRuntime(ctx),
        }
    )
    assert "/data/downloads/p.png" in out
    cmd, kw = patch_builder.calls[0]
    assert cmd == "fetch_resource"
    assert kw["save_path"] == "/data/downloads/p.png"


@pytest.mark.asyncio
async def test_fetch_resource_rejects_invalid_save_path_before_browser_transfer(patch_builder):
    ctx = types.SimpleNamespace(browser=object(), vfs_run=object(), chat_id="c1")
    out = await _tool("browser_fetch_resource").ainvoke(
        {
            "url": "https://x.test/p.png",
            "save_path": "/run/downloads/p.png",
            "runtime": FakeRuntime(ctx),
        }
    )
    assert "under the current chat workspace /data/ folder" in out
    assert patch_builder.calls == []


@pytest.mark.asyncio
async def test_browser_observation_error_becomes_standard_error_artifact(monkeypatch):
    class MissingTabBrowser:
        async def _send(self, _cmd, **_kw):
            return {"ok": False, "error": "tab 99 not found"}

    monkeypatch.setattr(_common, "build_agent_browser", lambda ctx: MissingTabBrowser())

    async def fake_binding(_ctx):
        return {"status": "attached"}

    monkeypatch.setattr(_common, "_load_browser_binding", fake_binding)
    ctx = types.SimpleNamespace(browser=object(), tenant_id="tenant_1", chat_id="chat_1")

    content, artifact = await _common_tool_click(ctx)

    assert "[tab_not_found]" in content
    assert "browser_tab(action='list_open')" in content
    assert artifact["status"] == "error"
    assert artifact["error"]["code"] == "tab_not_found"
    assert artifact["error"]["info"]["recovery_hint"]


async def _common_tool_click(ctx):
    """Invoke the decorated worker to assert both model and frontend channels."""
    from vibecanvas_api.services.platform_mcp.browser_tools.act import _click

    return await _click("h1", "", "", "", 0, FakeRuntime(ctx))


def test_all_tool_names_present():
    names = {t.name for t in bt.BROWSER_TOOLS}
    expected = {
        # read
        "browser_navigate", "browser_snapshot", "browser_read_text",
        "browser_query", "browser_get_attribute",
        "browser_get_html", "browser_take_screenshot", "browser_scroll",
        "browser_wait_for", "browser_tab", "browser_fetch_resource",
        # act
        "browser_click", "browser_type",
        "browser_select_option", "browser_press_key",
        # session
        "browser_session_status", "browser_check_login",
        "browser_start_session", "browser_end_session",
    }
    assert expected <= names
    # read_fields / list_tabs folded away; fill merged into type(replace=);
    # submit dropped (use press Enter / click); fetch_resource and end_session
    # are explicit V1 tools.
    assert len(bt.BROWSER_TOOLS) == 19
    assert {"browser_fill", "browser_submit", "browser_read_fields",
            "browser_list_tabs"}.isdisjoint(names)


def test_snapshot_render_includes_model_visible_handles():
    rendered = _render_snapshot({
        "data": {
            "dom": "Checkout",
            "handles": [
                {"handle": "s1_h0", "role": "button", "name": "Submit", "selector": "#submit"},
            ],
        },
    }, None)
    assert "Checkout" in rendered.content
    assert '[s1_h0] button "Submit" selector=#submit' in rendered.content
    assert "1 interactive element" in rendered.abstract


def test_start_session_render_exposes_stable_tab_but_not_internal_fencing_state():
    rendered = _render_start_session({
        "tab": 201,
        "data": {
            "started": True,
            "browser_session_id": "brs_1",
            "session_generation": 3,
        },
    }, None)
    assert "Controlling tab: 201" in rendered.content
    assert "brs_1" not in rendered.content
    assert "Session generation" not in rendered.content


def test_tab_render_exposes_stable_tab_for_followup_tools():
    rendered = _render_tab({
        "data": {
            "tab": 202,
            "url": "https://example.test/page",
            "controlled": True,
        },
    }, None)
    assert "Tab: 202" in rendered.content
    assert "URL: https://example.test/page" in rendered.content
    assert "Controlled: True" in rendered.content
