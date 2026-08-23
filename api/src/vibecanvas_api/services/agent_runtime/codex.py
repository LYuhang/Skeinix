"""Codex app-server Runtime adapter executed inside the user sandbox."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import re
import shutil
import uuid
from collections import defaultdict, deque
from collections.abc import Hashable
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit

from vibecanvas_engine.sandbox_bus import MSG_RUNTIME_CONTROL

from vibecanvas_api.agents.middleware.user_approval import requires_user_approval
from vibecanvas_api.services.agent_runtime.codex_app_server import (
    CodexAppServer,
    CodexAppServerError,
)
from vibecanvas_api.services.agent_runtime.codex_debug_snapshot import (
    capture_codex_debug_snapshot,
)
from vibecanvas_api.services.agent_runtime.codex_mcp_hub_gateway import (
    CodexMcpHubGateway,
)
from vibecanvas_api.services.agent_runtime.control import RuntimeControlRouter
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeEvent,
    RuntimeTurnRequest,
    RuntimeType,
)
from vibecanvas_api.services.agent_runtime.tool_invocation import (
    finish_tool_invocation,
    start_tool_invocation,
)

_BROKER_PROVIDER_ID = "vibecanvas_runtime_model"
# Agent Runtime sandboxes intentionally do not mount the workflow-only /run
# tier. /tmp is an isolated tmpfs in every gVisor container, making it the
# correct crash-discarded location for a short-lived broker capability.
_BROKER_CAPABILITY_DIR = "/tmp/vibecanvas-runtime"
_BROKER_CAPABILITY_PATH = f"{_BROKER_CAPABILITY_DIR}/model-capability"
_MAX_BROKER_CAPABILITY_BYTES = 16 * 1024
_MISSING_ROLLOUT_MESSAGE = re.compile(
    r"no rollout found for thread id \S+",
    flags=re.IGNORECASE,
)

# Codex-native items that are user-observable work.  They are projected through
# the same portable message/tool lifecycle as every other Runtime while keeping
# the native kind in ``invocation.native_kind`` for optional Codex presenters.
# ``userMessage`` is product-owned and would duplicate the optimistic user
# bubble; ``plan`` uses the authoritative ``turn/plan/updated`` snapshot;
# ``agentMessage`` has the ordinary message lifecycle.
_VISIBLE_TOOL_ITEM_KINDS = frozenset({
    "collabAgentToolCall",
    "commandExecution",
    "contextCompaction",
    "dynamicToolCall",
    "enteredReviewMode",
    "exitedReviewMode",
    "fileChange",
    "hookPrompt",
    "imageGeneration",
    "imageView",
    "mcpToolCall",
    "reasoning",
    "sleep",
    "subAgentActivity",
    "webSearch",
})
_CODEX_SUPPRESSED_ITEM_KINDS = frozenset({"plan", "userMessage"})
_CODEX_PROJECTED_ITEM_KINDS = (
    _VISIBLE_TOOL_ITEM_KINDS | {"agentMessage"}
)

# Expected app-server notifications that are either lifecycle duplicates or
# owned by account/configuration surfaces. Keeping this explicit means a Codex
# upgrade produces a bounded warning for a genuinely new method instead of
# silently discarding it. Hidden reasoning deltas are intentionally suppressed.
_CODEX_SUPPRESSED_NOTIFICATIONS = frozenset({
    "account/login/completed",
    "account/rateLimits/updated",
    "account/updated",
    "app/list/updated",
    "command/exec/outputDelta",
    "externalAgentConfig/import/completed",
    "externalAgentConfig/import/progress",
    "fs/changed",
    "fuzzyFileSearch/sessionCompleted",
    "fuzzyFileSearch/sessionUpdated",
    "item/autoApprovalReview/completed",
    "item/autoApprovalReview/started",
    "item/commandExecution/terminalInteraction",
    "item/plan/delta",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/textDelta",
    "mcpServer/oauthLogin/completed",
    "mcpServer/startupStatus/updated",
    "model/safetyBuffering/updated",
    "process/exited",
    "process/outputDelta",
    "remoteControl/status/changed",
    "serverRequest/resolved",
    "skills/changed",
    "thread/archived",
    "thread/closed",
    "thread/compacted",
    "thread/deleted",
    "thread/environment/connected",
    "thread/environment/disconnected",
    "thread/goal/cleared",
    "thread/goal/updated",
    "thread/name/updated",
    "thread/settings/updated",
    "thread/started",
    "thread/status/changed",
    "thread/realtime/closed",
    "thread/realtime/error",
    "thread/realtime/itemAdded",
    "thread/realtime/outputAudio/delta",
    "thread/realtime/sdp",
    "thread/realtime/started",
    "thread/realtime/transcript/delta",
    "thread/realtime/transcript/done",
    "thread/unarchived",
    "turn/diff/updated",
    "turn/moderationMetadata",
    "turn/started",
    "windowsSandbox/setupCompleted",
})

_CODEX_WARNING_NOTIFICATIONS = frozenset({
    "configWarning",
    "deprecationNotice",
    "guardianWarning",
    "warning",
    "windows/worldWritableWarning",
})

_CODEX_PROJECTED_NOTIFICATIONS = frozenset({
    "error",
    "hook/completed",
    "hook/started",
    "item/agentMessage/delta",
    "item/commandExecution/outputDelta",
    "item/completed",
    "item/fileChange/outputDelta",
    "item/fileChange/patchUpdated",
    "item/mcpToolCall/progress",
    "item/reasoning/summaryTextDelta",
    "item/started",
    "mcpServer/startupStatus/updated",
    "model/rerouted",
    "model/safetyBuffering/updated",
    "model/verification",
    "thread/tokenUsage/updated",
    "turn/completed",
    "turn/plan/updated",
    *_CODEX_WARNING_NOTIFICATIONS,
})
_CODEX_RECOGNIZED_NOTIFICATIONS = (
    _CODEX_SUPPRESSED_NOTIFICATIONS | _CODEX_PROJECTED_NOTIFICATIONS
)

_CODEX_INTERACTIVE_SERVER_REQUESTS = frozenset({
    "item/tool/requestUserInput",
    "mcpServer/elicitation/request",
})
_CODEX_APPROVAL_SERVER_REQUESTS = frozenset({
    "item/commandExecution/requestApproval",
    "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
})
# These requests belong to capabilities Skeinix does not advertise or to
# legacy APIs superseded by the Turn-scoped methods above. They receive an
# immediate bounded JSON-RPC error, never a silent wait.
_CODEX_REJECTED_SERVER_REQUESTS = frozenset({
    "account/chatgptAuthTokens/refresh",
    "applyPatchApproval",
    "attestation/generate",
    "currentTime/read",
    "execCommandApproval",
    "item/tool/call",
})


def _codex_executable() -> str:
    configured = os.environ.get("CODEX_CLI_PATH", "").strip()
    executable = configured or shutil.which("codex") or ""
    if not executable or not os.path.isfile(executable) or not os.access(executable, os.X_OK):
        raise RuntimeError("codex_cli_unavailable_in_sandbox")
    return executable


def _codex_env(runtime_root: str) -> dict[str, str]:
    allowed = {
        "PATH",
        "LANG",
        "LC_ALL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CODEX_CA_CERTIFICATE",
        "CODEX_APP_SERVER_JSONL_LIMIT_BYTES",
    }
    env = {key: value for key, value in os.environ.items() if key in allowed}
    # The browser policy gateway is bound to this exact loopback address inside
    # the sandbox. Keep that hop away from HTTP(S)_PROXY while leaving every
    # non-loopback destination on the fail-closed egress path. Do not inherit a
    # broader host NO_PROXY list into the sandbox.
    env["NO_PROXY"] = "127.0.0.1"
    env["CODEX_HOME"] = runtime_root
    env["CODEX_SQLITE_HOME"] = runtime_root
    # Codex discovers Agent Skills from $HOME/.agents/skills. CODEX_HOME stays
    # dedicated to this Chat's thread state; account credentials are forbidden.
    env["HOME"] = "/runtime/home"
    return env


def _broker_model_config(request: RuntimeTurnRequest) -> tuple[str, dict[str, Any]]:
    """Build a credential-free Codex custom-provider configuration.

    The bearer capability itself is written to a volatile 0600 file and read
    by Codex's command-backed provider auth. The config can therefore remain in
    a resident app-server without persisting a provider credential or a user
    account token in Codex thread state.
    """
    model = request.model if isinstance(request.model, dict) else {}
    model_id = str(model.get("id") or "").strip()
    base_url = str(model.get("base_url") or "").strip()
    capability = str(model.get("api_key") or "").strip()
    if not model_id or len(model_id.encode("utf-8")) > 512:
        raise RuntimeError("codex_broker_model_invalid")
    if (
        not capability
        or len(capability.encode("utf-8")) > _MAX_BROKER_CAPABILITY_BYTES
        or any(ch.isspace() for ch in capability)
    ):
        raise RuntimeError("codex_model_capability_invalid")
    parts = urlsplit(base_url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not parts.path.rstrip("/").endswith("/api/internal/runtime-model/v1")
    ):
        raise RuntimeError("codex_model_broker_url_invalid")
    return capability, {
        "model_provider": _BROKER_PROVIDER_ID,
        "model_providers": {
            _BROKER_PROVIDER_ID: {
                "name": "Skeinix Runtime Model Broker",
                "base_url": base_url.rstrip("/"),
                "wire_api": "responses",
                # Newer Codex releases flatten namespace tools natively for
                # compatible custom providers. The host broker retains a
                # capability-preserving fallback for older bundled releases.
                "namespace_tools": False,
                "request_max_retries": 4,
                "stream_max_retries": 5,
                "stream_idle_timeout_ms": 300_000,
                "auth": {
                    "command": "/bin/cat",
                    "args": [_BROKER_CAPABILITY_PATH],
                    "timeout_ms": 1_000,
                    # Re-read for every practical model request so a resident
                    # app-server cannot carry a previous Turn's lease forward.
                    "refresh_interval_ms": 1,
                },
            }
        },
    }


def _uses_chatgpt_account(request: RuntimeTurnRequest) -> bool:
    model = request.model if isinstance(request.model, dict) else {}
    return model.get("connection_type") == "chatgpt_account"


def _install_broker_capability(capability: str) -> None:
    os.makedirs(_BROKER_CAPABILITY_DIR, mode=0o700, exist_ok=True)
    os.chmod(_BROKER_CAPABILITY_DIR, 0o700)
    temporary = f"{_BROKER_CAPABILITY_PATH}.{os.getpid()}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(temporary, flags, 0o600)
        try:
            payload = capability.encode("utf-8")
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:  # pragma: no cover - defensive kernel boundary
                    raise OSError("failed to write Runtime model capability")
                payload = payload[written:]
        finally:
            os.close(descriptor)
        os.replace(temporary, _BROKER_CAPABILITY_PATH)
        os.chmod(_BROKER_CAPABILITY_PATH, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _remove_broker_capability() -> None:
    try:
        os.unlink(_BROKER_CAPABILITY_PATH)
    except FileNotFoundError:
        pass


def _remove_forbidden_account_cache(runtime_root: str) -> None:
    """Ensure Chat-scoped Codex state never retains an account credential."""
    auth_path = os.path.join(runtime_root, "auth.json")
    try:
        if os.path.isdir(auth_path) and not os.path.islink(auth_path):
            raise RuntimeError("codex_runtime_auth_path_invalid")
        os.unlink(auth_path)
    except FileNotFoundError:
        pass


def _prepare_codex_skills(request: RuntimeTurnRequest) -> None:
    """Project immutable /skills revisions into Codex's native Skill path."""
    skills_root = "/runtime/home/.agents/skills"
    os.makedirs(skills_root, mode=0o700, exist_ok=True)
    if not os.path.isdir(skills_root):
        if request.skills:
            raise RuntimeError("codex_skill_directory_unavailable")
        return
    expected: set[str] = set()
    for descriptor in request.skills:
        link_name = descriptor.skill_id
        expected.add(link_name)
        link_path = os.path.join(skills_root, link_name)
        if os.path.lexists(link_path):
            if (
                os.path.islink(link_path)
                and os.readlink(link_path) == descriptor.root_path
            ):
                continue
            if os.path.isdir(link_path) and not os.path.islink(link_path):
                shutil.rmtree(link_path)
            else:
                os.unlink(link_path)
        os.symlink(descriptor.root_path, link_path, target_is_directory=True)
    for existing in os.listdir(skills_root):
        if existing not in expected:
            path = os.path.join(skills_root, existing)
            if os.path.isdir(path) and not os.path.islink(path):
                shutil.rmtree(path)
            else:
                os.unlink(path)


def _approval_policy(mode: str) -> str:
    return {
        "always_allow": "never",
        "always_ask": "untrusted",
        "agent": "on-request",
    }[mode]


def _platform_mcp_tool_timeout_s() -> float:
    raw = os.environ.get("CODEX_PLATFORM_MCP_TOOL_TIMEOUT_S", "").strip()
    if not raw:
        return 24 * 60 * 60
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(
            "CODEX_PLATFORM_MCP_TOOL_TIMEOUT_S must be a number"
        ) from exc
    if value < 60:
        raise RuntimeError(
            "CODEX_PLATFORM_MCP_TOOL_TIMEOUT_S must be at least 60 seconds"
        )
    return value


# Kept as a module-level name for focused adapter tests; implementation is the
# shared Runtime-neutral router used by every sandbox adapter.
_RuntimeControlRouter = RuntimeControlRouter


def _mcp_item_key(tool_name: str, arguments: dict[str, Any]) -> tuple[str, Hashable]:
    """Build the stable key shared by Codex item events and the MCP gateway."""
    return (
        tool_name,
        json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    )


class _McpItemCorrelator:
    """Correlate a gateway CallTool request with its Codex app-server item.

    Codex does not include its ``mcpToolCall`` item id in the downstream MCP
    request. The item notification and gateway request are delivered on
    independent tasks, so either one can arrive first. Matching queues preserve
    order for identical concurrent calls without conflating the gateway request
    id (control routing) with the Codex item id (transcript/UI projection).
    """

    def __init__(self) -> None:
        self._items: dict[tuple[str, Hashable], deque[str]] = defaultdict(deque)
        self._waiters: dict[
            tuple[str, Hashable], deque[asyncio.Future[str]]
        ] = defaultdict(deque)

    def register(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        item_id: str,
    ) -> None:
        key = _mcp_item_key(tool_name, arguments)
        waiters = self._waiters.get(key)
        while waiters:
            waiter = waiters.popleft()
            if not waiter.done():
                waiter.set_result(item_id)
                if not waiters:
                    self._waiters.pop(key, None)
                return
        self._items[key].append(item_id)

    async def wait(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        timeout_s: float = 10.0,
    ) -> str:
        key = _mcp_item_key(tool_name, arguments)
        items = self._items.get(key)
        if items:
            item_id = items.popleft()
            if not items:
                self._items.pop(key, None)
            return item_id

        future = asyncio.get_running_loop().create_future()
        self._waiters[key].append(future)
        try:
            return await asyncio.wait_for(future, timeout=timeout_s)
        except TimeoutError as exc:
            raise RuntimeError(
                "Codex MCP tool call could not be correlated with its Runtime item: "
                f"{tool_name}"
            ) from exc
        finally:
            waiters = self._waiters.get(key)
            if waiters:
                try:
                    waiters.remove(future)
                except ValueError:
                    pass
                if not waiters:
                    self._waiters.pop(key, None)

    def cancel(self) -> None:
        for waiters in self._waiters.values():
            for waiter in waiters:
                if not waiter.done():
                    waiter.cancel()
        self._waiters.clear()
        self._items.clear()


def _missing_rollout_error(exc: CodexAppServerError) -> bool:
    """Match only Codex's explicit stale native-thread response.

    A broad resume fallback would hide authentication, protocol, storage, and
    permission defects. This one response means the platform checkpoint points
    at a rollout that is not present in the Chat Runtime volume, so starting a
    replacement native thread is the only recoverable action.
    """

    return (
        exc.code == "codex_app_server_request_failed"
        and _MISSING_ROLLOUT_MESSAGE.fullmatch(str(exc).strip()) is not None
    )


def _turn_input(
    request: RuntimeTurnRequest,
    *, recovered_missing_rollout: bool = False,
) -> list[dict[str, Any]]:
    content = str(request.message.get("content") or "")
    instructions = [
        item
        for item in request.instructions
        if item.kind == "command_context" and item.activated_this_turn
    ]
    if request.runtime_state_ref is None or recovered_missing_rollout:
        # A prior attempt may have persisted sticky capability activation but
        # failed before Codex created its first thread. Seed the new native
        # history with every active command in that case.
        instructions = [
            item
            for item in request.instructions
            if item.kind == "command_context"
        ]
    contexts = [item.content for item in instructions]
    if recovered_missing_rollout:
        contexts.insert(
            0,
            "The previous native Codex thread was unavailable after Runtime "
            "recovery. Continue from the current request and treat durable "
            "files under /data, /memory, and /logs as the source of truth. "
            "Do not invent results from the unavailable native transcript.",
        )
    if contexts:
        content = (
            "<system-reminder>\n"
            + "\n\n".join(contexts)
            + "\n</system-reminder>\n\n<user-message>\n"
            + content
            + "\n</user-message>"
        )
    result: list[dict[str, Any]] = [{"type": "text", "text": content}]
    for attachment in request.attachments:
        path = attachment.get("path")
        name = attachment.get("name")
        kind = attachment.get("type")
        if not isinstance(path, str) or not path.startswith("/"):
            continue
        if kind == "image":
            result.append({"type": "localImage", "path": path})
        elif isinstance(name, str) and name:
            result.append({"type": "mention", "name": name, "path": path})
    return result


def _interactive_artifact_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    """Find render_interactive's structured MCP result without wire coupling."""
    queue: deque[Any] = deque(
        [
            item.get("result"),
            item.get("structuredContent"),
            item.get("structured_content"),
        ]
    )
    seen: set[int] = set()
    while queue:
        value = queue.popleft()
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except Exception:
                continue
        if isinstance(value, list):
            queue.extend(value)
            continue
        if not isinstance(value, dict):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        meta = value.get("meta")
        payload = value.get("payload")
        if (
            isinstance(meta, dict)
            and meta.get("tool") == "render_interactive"
            and isinstance(payload, dict)
            and payload.get("kind") == "interactive_artifact"
        ):
            return copy.deepcopy(value)
        if (
            value.get("kind") == "interactive_artifact"
            and isinstance(value.get("artifact_id"), str)
        ):
            # A few MCP clients expose structuredContent as the definition
            # rather than the platform's two-channel artifact envelope.
            return {
                "schema_version": 1,
                "status": "success",
                "error": None,
                "content": "",
                "content_abstract": "",
                "ref": f"tool://render_interactive/{value['artifact_id']}",
                "artifact": {"kind": "interactive_artifact", "target": {}},
                "payload": {
                    "kind": "interactive_artifact",
                    "artifact": copy.deepcopy(value),
                    "artifact_preview": None,
                    "artifact_ref": None,
                    "hitl_request_id": value.get("hitl_request_id"),
                },
                "meta": {"tool": "render_interactive"},
            }
        queue.extend(value.values())
    return None


def _tool_projection(
    item: dict[str, Any],
) -> tuple[str, str, str, dict[str, Any] | None]:
    kind = str(item.get("type") or "tool")
    if kind == "reasoning":
        # Codex explicitly separates a user-displayable summary from private
        # reasoning content.  Never serialize ``content`` into product events.
        summary = item.get("summary")
        summary_parts = [
            str(part).strip()
            for part in (summary if isinstance(summary, list) else [])
            if str(part).strip()
        ]
        return (
            "reasoning_summary",
            json.dumps({"summary_parts": len(summary_parts)}, ensure_ascii=False),
            "\n\n".join(summary_parts) or "Reasoning step completed",
            None,
        )
    if kind == "imageView":
        path = str(item.get("path") or "")
        return (
            "view_image",
            json.dumps({"path": path}, ensure_ascii=False),
            "Image opened" if path else "Image view completed",
            None,
        )
    if kind == "commandExecution":
        return "shell", json.dumps(
            {"command": item.get("command"), "cwd": item.get("cwd")},
            ensure_ascii=False,
        ), str(item.get("aggregatedOutput") or ""), None
    if kind == "fileChange":
        return "file_change", json.dumps(
            {"changes": item.get("changes") or []}, ensure_ascii=False
        ), str(item.get("status") or ""), None
    if kind == "mcpToolCall":
        name = str(item.get("tool") or "mcp_tool")
        arguments = json.dumps(item.get("arguments") or {}, ensure_ascii=False)
        result = item.get("result") if item.get("result") is not None else item.get("error")
        return (
            name,
            arguments,
            json.dumps(result, ensure_ascii=False, default=str),
            _interactive_artifact_from_item(item),
        )
    if kind == "dynamicToolCall":
        name = str(item.get("tool") or "dynamic_tool")
        return (
            name,
            json.dumps(item.get("arguments") or {}, ensure_ascii=False),
            json.dumps(item.get("contentItems") or [], ensure_ascii=False, default=str),
            _interactive_artifact_from_item(item),
        )
    if kind == "webSearch":
        return (
            "web_search",
            json.dumps(
                {
                    "query": item.get("query") or "",
                    "action": item.get("action"),
                },
                ensure_ascii=False,
            ),
            json.dumps(item.get("results") or [], ensure_ascii=False, default=str),
            None,
        )
    if kind == "collabAgentToolCall":
        return (
            "collab_agent",
            json.dumps(
                {
                    "operation": item.get("tool"),
                    "prompt": item.get("prompt"),
                    "model": item.get("model"),
                    "reasoning_effort": item.get("reasoningEffort"),
                    "receiver_count": len(item.get("receiverThreadIds") or []),
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "status": item.get("status"),
                    "agents": item.get("agentsStates") or {},
                },
                ensure_ascii=False,
                default=str,
            ),
            None,
        )
    if kind == "subAgentActivity":
        return (
            "subagent_activity",
            json.dumps(
                {
                    "agent": item.get("agentPath"),
                    "activity": item.get("kind"),
                },
                ensure_ascii=False,
            ),
            str(item.get("kind") or "Subagent activity completed"),
            None,
        )
    if kind == "imageGeneration":
        return (
            "generate_image",
            json.dumps(
                {"revised_prompt": item.get("revisedPrompt")},
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "status": item.get("status"),
                    "result": item.get("result"),
                    "saved_path": item.get("savedPath"),
                },
                ensure_ascii=False,
                default=str,
            ),
            None,
        )
    if kind == "sleep":
        return (
            "wait",
            json.dumps({"duration_ms": item.get("durationMs")}, ensure_ascii=False),
            "Wait completed",
            None,
        )
    if kind in {"enteredReviewMode", "exitedReviewMode"}:
        entered = kind == "enteredReviewMode"
        return (
            "review_mode",
            json.dumps({"state": "entered" if entered else "exited"}),
            str(item.get("review") or ("Review started" if entered else "Review finished")),
            None,
        )
    if kind == "contextCompaction":
        return "context_compaction", "{}", "Conversation context compacted", None
    if kind == "hookPrompt":
        # Hook prompt fragments can contain platform/system instructions.  The
        # user should see that a hook participated, but never its injected text.
        run = item.get("run")
        if isinstance(run, dict):
            status = str(run.get("status") or "running")
            return (
                "runtime_hook",
                json.dumps(
                    {
                        "event": run.get("eventName"),
                        "scope": run.get("scope"),
                        "mode": run.get("executionMode"),
                    },
                    ensure_ascii=False,
                ),
                str(run.get("statusMessage") or f"Hook {status}"),
                None,
            )
        fragments = item.get("fragments")
        count = len(fragments) if isinstance(fragments, list) else 0
        return (
            "runtime_hook",
            json.dumps({"fragment_count": count}, ensure_ascii=False),
            "Runtime hook context applied",
            None,
        )
    return kind, "{}", str(item.get("status") or ""), None


def _tool_completion_status(item: dict[str, Any]) -> str:
    """Normalize SDK-native terminal states to the portable UI contract."""
    status = str(item.get("status") or "").strip().lower()
    if status in {
        "blocked",
        "cancelled",
        "canceled",
        "declined",
        "error",
        "errored",
        "failed",
    }:
        return "error"
    if item.get("success") is False or item.get("error"):
        return "error"
    return "done"


def _safe_codex_notice(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Project user-facing native notices without paths or raw protocol data."""
    if method == "error":
        error = params.get("error")
        error = error if isinstance(error, dict) else {}
        message = str(error.get("message") or "Codex encountered a Runtime error.")
        will_retry = bool(params.get("willRetry"))
        return {
            "level": "warning" if will_retry else "error",
            "code": "codex_runtime_retry" if will_retry else "codex_runtime_error",
            "message": message.strip()[:500],
            "runtime_type": "codex",
            "native_kind": method,
            "retrying": will_retry,
            "turn_disposition": "continue",
        }
    if method in _CODEX_WARNING_NOTIFICATIONS:
        message = str(
            params.get("message")
            or params.get("summary")
            or "Codex reported a Runtime warning."
        ).strip()
        return {
            "level": "warning",
            "code": "codex_runtime_warning",
            "message": message[:500],
            "runtime_type": "codex",
            "native_kind": method,
            "turn_disposition": "continue",
        }
    if method == "model/rerouted":
        from_model = str(params.get("fromModel") or "the selected model")
        to_model = str(params.get("toModel") or "another model")
        return {
            "level": "info",
            "code": "codex_model_rerouted",
            "message": f"Codex switched from {from_model} to {to_model}.",
            "runtime_type": "codex",
            "native_kind": method,
            "reason": str(params.get("reason") or "")[:100],
            "turn_disposition": "continue",
        }
    if method == "model/verification":
        return {
            "level": "info",
            "code": "codex_model_verified",
            "message": "Codex completed an additional model access verification.",
            "runtime_type": "codex",
            "native_kind": method,
            "turn_disposition": "continue",
        }
    if method == "model/safetyBuffering/updated" and bool(
        params.get("showBufferingUi")
    ):
        return {
            "level": "info",
            "code": "codex_safety_buffering",
            "message": "Codex is completing an additional safety check.",
            "runtime_type": "codex",
            "native_kind": method,
            "turn_disposition": "continue",
        }
    if (
        method == "mcpServer/startupStatus/updated"
        and str(params.get("status") or "") in {"failed", "cancelled"}
    ):
        name = str(params.get("name") or "MCP server")[:100]
        status = str(params.get("status") or "failed")
        return {
            "level": "warning",
            "code": "codex_mcp_startup_failed",
            "message": f"{name} did not start ({status}).",
            "runtime_type": "codex",
            "native_kind": method,
            "mcp_server": name,
            "status": status,
            "turn_disposition": "continue",
        }
    return None


def _file_change_progress(params: dict[str, Any]) -> str:
    """Return a bounded patch projection without host filesystem paths."""
    changes = params.get("changes")
    changes = changes if isinstance(changes, list) else []
    safe_changes: list[dict[str, str]] = []
    remaining = 64 * 1024
    for change in changes[:100]:
        if not isinstance(change, dict):
            continue
        raw_path = str(change.get("path") or "")
        if raw_path.startswith("/data/"):
            path = raw_path.removeprefix("/data/")
        elif raw_path.startswith("/mount/"):
            path = raw_path.removeprefix("/mount/")
        else:
            path = os.path.basename(raw_path)
        diff = str(change.get("diff") or "")[:remaining]
        remaining -= len(diff)
        safe_changes.append({
            "path": path[:500],
            "kind": str(change.get("kind") or "update")[:40],
            "diff": diff,
        })
        if remaining <= 0:
            break
    return json.dumps(
        {"changes": safe_changes, "truncated": remaining <= 0},
        ensure_ascii=False,
    )


def _approval_prompt(method: str, params: dict[str, Any]) -> tuple[str, str]:
    if method == "item/commandExecution/requestApproval":
        return "Approve command", str(params.get("command") or "Run this command?")
    if method == "item/fileChange/requestApproval":
        return "Approve file changes", "Allow Codex to apply the proposed file changes?"
    if method == "item/permissions/requestApproval":
        return (
            "Approve additional permissions",
            str(params.get("reason") or "Allow the requested sandbox permissions for this turn?"),
        )
    return "Approval required", str(params.get("reason") or "Allow this operation?")


def _approval_response(
    method: str,
    params: dict[str, Any],
    action: str,
) -> dict[str, Any]:
    """Build the method-specific app-server response for one approval."""
    if method == "item/permissions/requestApproval":
        permissions = params.get("permissions")
        granted = dict(permissions) if action == "approve" and isinstance(permissions, dict) else {}
        return {"permissions": granted, "scope": "turn"}
    decision = {
        "approve": "accept",
        "deny": "decline",
        "cancel": "cancel",
    }.get(action, "decline")
    return {"decision": decision}


def _schema_options(schema: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    """Normalize the bounded select subset shared by MCP elicitation schemas."""
    values = schema.get("enum")
    labels = schema.get("enumNames")
    options: list[dict[str, str]] = []
    if isinstance(values, list):
        for index, value in enumerate(values):
            label = (
                str(labels[index])
                if isinstance(labels, list) and index < len(labels)
                else str(value)
            )
            options.append({"label": label, "value": str(value)})
    for branch_name in ("oneOf", "anyOf"):
        branches = schema.get(branch_name)
        if not isinstance(branches, list):
            continue
        for branch in branches:
            if not isinstance(branch, dict) or "const" not in branch:
                continue
            value = branch["const"]
            options.append({
                "label": str(branch.get("title") or value),
                "value": str(value),
            })
    schema_type = schema.get("type")
    if schema_type == "boolean" and not options:
        options = [
            {"label": "Yes", "value": "true"},
            {"label": "No", "value": "false"},
        ]
    return options[:100], schema_type == "array"


def _interaction_definition(
    method: str,
    params: dict[str, Any],
    *,
    artifact_id: str,
    hitl_request_id: str,
) -> dict[str, Any]:
    """Convert native input/elicitation requests into one portable form contract."""
    questions: list[dict[str, Any]] = []
    title = "Input required"
    message = "Provide the requested information to continue."
    url: str | None = None
    if method == "item/tool/requestUserInput":
        native_questions = params.get("questions")
        for native in native_questions if isinstance(native_questions, list) else []:
            if not isinstance(native, dict):
                continue
            question_id = str(native.get("id") or "").strip()
            question = str(native.get("question") or "").strip()
            if not question_id or not question:
                continue
            native_options = native.get("options")
            options = [
                {
                    "label": str(option.get("label") or ""),
                    "value": str(option.get("label") or ""),
                    "description": str(option.get("description") or ""),
                }
                for option in (
                    native_options if isinstance(native_options, list) else []
                )
                if isinstance(option, dict) and str(option.get("label") or "").strip()
            ]
            questions.append({
                "id": question_id,
                "label": question,
                "description": str(native.get("header") or ""),
                "secret": bool(native.get("isSecret")),
                "multiple": False,
                "options": options,
            })
    else:
        server_name = str(params.get("serverName") or "MCP server").strip()
        title = f"{server_name} needs input"
        message = str(params.get("message") or message)
        mode = str(params.get("mode") or "form")
        if mode == "url":
            candidate = str(params.get("url") or "")
            parsed = urlsplit(candidate)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                url = candidate
        else:
            requested = params.get("requestedSchema")
            requested = requested if isinstance(requested, dict) else {}
            properties = requested.get("properties")
            properties = properties if isinstance(properties, dict) else {}
            for name, raw_schema in properties.items():
                if not isinstance(raw_schema, dict):
                    continue
                options, multiple = _schema_options(raw_schema)
                questions.append({
                    "id": str(name),
                    "label": str(raw_schema.get("title") or name),
                    "description": str(raw_schema.get("description") or ""),
                    "secret": bool(raw_schema.get("writeOnly"))
                    or str(raw_schema.get("format") or "") == "password",
                    "multiple": multiple,
                    "options": options,
                })
    hide_result = any(bool(question.get("secret")) for question in questions)
    return {
        "kind": "interactive_artifact",
        "schema_version": 1,
        "artifact_id": artifact_id,
        "hitl_request_id": hitl_request_id,
        "title": title,
        "component_type": "user_input",
        "props": {
            "message": message,
            "questions": questions,
            **({"url": url} if url else {}),
        },
        "interaction_schema": {
            "interaction_type": "input",
            "submit_label": "Submit",
            "cancel_label": "Cancel",
            "hide_result": hide_result,
        },
        "completion_mode": "wait_for_submit",
        "height": 360,
        "placement": "inline",
        "preview": {"mode": "none"},
        "widget_state": {},
        "interaction_state": {
            "is_interacted": False,
            "status": "pending",
            "result": {},
        },
    }


def _coerce_elicitation_content(
    requested_schema: Any,
    widget_state: dict[str, Any],
) -> dict[str, Any]:
    schema = requested_schema if isinstance(requested_schema, dict) else {}
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    content: dict[str, Any] = {}
    for name, value in widget_state.items():
        field = properties.get(name)
        field = field if isinstance(field, dict) else {}
        field_type = field.get("type")
        if field_type == "boolean" and isinstance(value, str):
            content[name] = value.lower() == "true"
        elif field_type in {"integer", "number"} and isinstance(value, str):
            try:
                content[name] = int(value) if field_type == "integer" else float(value)
            except ValueError:
                content[name] = value
        else:
            content[name] = value
    return content


def _interaction_response(
    method: str,
    params: dict[str, Any],
    response: dict[str, Any],
) -> dict[str, Any]:
    payload = response.get("payload")
    payload = payload if isinstance(payload, dict) else {}
    interaction_result = payload.get("interaction_result")
    interaction_result = (
        interaction_result if isinstance(interaction_result, dict) else {}
    )
    widget_state = interaction_result.get("widget_state")
    if not isinstance(widget_state, dict):
        decision_payload = payload.get("decision_payload")
        decision_payload = (
            decision_payload if isinstance(decision_payload, dict) else {}
        )
        widget_state = decision_payload.get("widget_state")
    widget_state = widget_state if isinstance(widget_state, dict) else {}
    action = str(response.get("action") or "cancel")
    if method == "item/tool/requestUserInput":
        native_questions = params.get("questions")
        question_ids = [
            str(question.get("id"))
            for question in (
                native_questions if isinstance(native_questions, list) else []
            )
            if isinstance(question, dict) and question.get("id")
        ]
        return {
            "answers": {
                question_id: {
                    "answers": (
                        [str(value) for value in widget_state.get(question_id, [])]
                        if isinstance(widget_state.get(question_id), list)
                        else (
                            [str(widget_state[question_id])]
                            if action == "submit" and question_id in widget_state
                            else []
                        )
                    )
                }
                for question_id in question_ids
            }
        }
    if action != "submit":
        return {"action": "cancel" if action == "cancel" else "decline"}
    return {
        "action": "accept",
        "content": _coerce_elicitation_content(
            params.get("requestedSchema"), widget_state
        ),
    }


def _normalize_codex_plan(plan: Any) -> list[dict[str, Any]]:
    """Map Codex's full plan snapshot to the product Todo contract."""
    status_map = {
        "pending": "pending",
        "inProgress": "in_progress",
        "completed": "done",
    }
    normalized: list[dict[str, Any]] = []
    if not isinstance(plan, list):
        return normalized
    for index, step in enumerate(plan, start=1):
        if not isinstance(step, dict):
            continue
        text = str(step.get("step") or "").strip()
        status = status_map.get(str(step.get("status") or ""))
        if not text or status is None:
            continue
        normalized.append({"id": index, "text": text, "status": status})
    return normalized


def create_codex_app_server(request: RuntimeTurnRequest) -> CodexAppServer:
    """Construct the Chat-scoped app-server owned by the resident Runtime."""
    return CodexAppServer(
        executable=_codex_executable(),
        env=_codex_env(request.runtime_root),
        cwd="/data" if os.path.isdir("/data") else "/mount",
        outer_sandboxed=True,
    )


async def run_codex_turn(
    channel,
    request: RuntimeTurnRequest,
    *,
    client: CodexAppServer | None = None,
    close_client: bool = True,
    mcp_hub: Any | None = None,
    mcp_adapter: Any | None = None,
    hub_gateway_registry: dict[str, Any] | None = None,
    resident_threads: dict[str, str] | None = None,
) -> None:
    setup_started = perf_counter()
    # Runtime requests cross a process/module boundary before reaching this
    # adapter. Compare enum values, not Python object identity: the sandbox may
    # load the protocol module through a distinct import root even though the
    # wire value is the same.
    if request.runtime_type != RuntimeType.CODEX:
        raise ValueError(f"unsupported runtime type: {request.runtime_type.value}")
    if not os.path.isdir("/runtime"):
        raise RuntimeError("Codex Runtime requires the private /runtime mount")
    os.makedirs(request.runtime_root, mode=0o700, exist_ok=True)
    account_mode = _uses_chatgpt_account(request)
    if account_mode:
        if not os.path.isfile(os.path.join(request.runtime_root, "auth.json")):
            raise RuntimeError("codex_account_not_connected")
        model_capability = None
        broker_model_config: dict[str, Any] = {}
    else:
        _remove_forbidden_account_cache(request.runtime_root)
        model_capability, broker_model_config = _broker_model_config(request)
    phase_started = perf_counter()
    _prepare_codex_skills(request)
    skills_prepare_ms = int((perf_counter() - phase_started) * 1000)

    seq = 1
    tool_invocations: dict[str, tuple[dict[str, Any], float]] = {}

    runtime_mcp_catalog: list[dict[str, Any]] = []

    def invocation_catalog(item: dict[str, Any], name: str) -> list[dict[str, Any]]:
        server_hint = str(item.get("server") or item.get("serverName") or "")
        return [
            entry
            for entry in runtime_mcp_catalog
            if (
                entry.get("name") == server_hint
                or any(
                    tool.get("name") == name
                    for tool in entry.get("tools") or []
                    if isinstance(tool, dict)
                )
            )
        ]

    def event(event_type: str, payload: dict[str, Any]) -> RuntimeEvent:
        nonlocal seq
        value = RuntimeEvent(
            event_id=f"rte_{uuid.uuid4().hex}",
            seq=seq,
            chat_id=request.chat_id,
            turn_id=request.turn_id,
            runtime_type=request.runtime_type,
            runtime_session_id=request.runtime_session_id,
            type=event_type,
            payload=payload,
        )
        seq += 1
        return value

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        from vibecanvas_engine.sandbox_bus import MSG_RUNTIME_EVENT

        async with emit_lock:
            await channel.send(
                {
                    "type": MSG_RUNTIME_EVENT,
                    "event": event(event_type, payload).model_dump(mode="json"),
                }
            )

    emit_lock = asyncio.Lock()
    client = client or create_codex_app_server(request)
    current: dict[str, str | None] = {"thread_id": None, "turn_id": None}
    control_router = _RuntimeControlRouter()
    mcp_item_correlator = _McpItemCorrelator()
    stop_event = asyncio.Event()
    active_hub_gateway: CodexMcpHubGateway | None = None
    debug_snapshot_task: asyncio.Task[str | None] | None = None

    async def finish_debug_snapshot() -> None:
        nonlocal debug_snapshot_task
        task = debug_snapshot_task
        debug_snapshot_task = None
        if task is None:
            return
        try:
            await task
        except Exception as exc:
            # Debug observability must never change Runtime behavior.
            print(f"⚠️  [codex] debug snapshot write failed: {exc}")

    async def controls() -> None:
        while True:
            message = await channel.recv()
            if message is None:
                stop_event.set()
                control_router.cancel()
                return
            if message.get("type") != MSG_RUNTIME_CONTROL:
                continue
            response = message.get("response") or {}
            if response.get("action") == "cancel" and not response.get("correlation"):
                stop_event.set()
                control_router.cancel()
                if current["thread_id"] and current["turn_id"]:
                    try:
                        await client.request(
                            "turn/interrupt",
                            {
                                "threadId": current["thread_id"],
                                "turnId": current["turn_id"],
                            },
                            timeout_s=10.0,
                        )
                    except Exception:
                        pass
                continue
            control_router.deliver(response)

    async def request_platform_approval(
        tool_name: str,
        arguments: dict[str, Any],
        runtime_request_id: str,
    ) -> str:
        runtime_item_id = await mcp_item_correlator.wait(tool_name, arguments)
        approval_seed = uuid.uuid5(
            uuid.NAMESPACE_URL,
            ":".join(
                (
                    "vibecanvas",
                    "codex-platform-mcp-approval",
                    request.chat_id,
                    request.turn_id,
                    runtime_request_id,
                )
            ),
        ).hex
        hitl_id = f"hitl_{approval_seed[:16]}"
        correlation = {
            "source": "platform_mcp",
            "runtime_request_id": runtime_request_id,
            "runtime_method": "tools/call",
            "runtime_thread_id": current["thread_id"],
            "runtime_turn_id": current["turn_id"],
            "runtime_item_id": runtime_item_id,
        }
        reason = str(arguments.get("approval_reason") or "").strip()
        prompt = reason or f"Allow the agent to execute {tool_name}?"
        waiter = asyncio.create_task(
            control_router.wait("platform_mcp", runtime_request_id)
        )
        try:
            await emit(
                "approval.requested",
                {
                    "hitl_request_id": hitl_id,
                    "hitl_type": "pre_tool_approval",
                    "title": f"Approve {tool_name}",
                    "prompt_text": prompt,
                    "actions": [
                        {"id": "approve", "label": "Approve", "variant": "primary"},
                        {"id": "deny", "label": "Deny", "variant": "secondary"},
                    ],
                    "agent_payload": {
                        "tool": tool_name,
                        "arguments": arguments,
                        "reason": reason,
                    },
                    "policy": {
                        "phase": "pre_tool",
                        "native_required": False,
                    },
                    "runtime_correlation": correlation,
                },
            )
            response = await waiter
            action = str(response.get("action") or "deny")
            if bool(response.get("persisted")):
                await emit(
                    "approval.resolved",
                    {
                        "hitl_request_id": hitl_id,
                        "status": {
                            "approve": "approved",
                            "deny": "denied",
                            "cancel": "cancelled",
                        }.get(action, "denied"),
                    },
                )
            return action
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def request_mcp_gateway(
        operation: str,
        server: Any,
        tool_name: str | None,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        request_id = f"mcpgw_{uuid.uuid4().hex}"
        correlation = {
            "source": "mcp_hub",
            "runtime_request_id": request_id,
            "runtime_method": operation,
            "runtime_thread_id": current["thread_id"],
            "runtime_turn_id": request.turn_id,
            "runtime_item_id": tool_name,
        }
        waiter = asyncio.create_task(
            control_router.wait("mcp_hub", request_id)
        )
        try:
            await emit("mcp.gateway.requested", {
                "request_id": request_id,
                "operation": operation,
                "server": server.name,
                "tool_name": tool_name,
                "arguments": dict(arguments),
                "execution_capability": (
                    request.mcp_execution_context.capability.get_secret_value()
                    if request.mcp_execution_context is not None
                    else ""
                ),
                "runtime_correlation": correlation,
            })
            response = await waiter
            if response.get("action") != "accepted":
                raise RuntimeError(
                    str(
                        response.get("error")
                        or "Host MCP Gateway rejected the request"
                    )
                )
            payload = response.get("payload")
            if not isinstance(payload, dict):
                raise RuntimeError(
                    "Host MCP Gateway returned an invalid payload"
                )
            return payload
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    phase_started = perf_counter()
    await client.start()
    app_server_start_ms = int((perf_counter() - phase_started) * 1000)
    if model_capability is not None:
        try:
            _install_broker_capability(model_capability)
        except Exception:
            if close_client:
                await client.close()
            raise
    control_task = asyncio.create_task(controls())
    open_messages: set[str] = set()
    message_had_delta: set[str] = set()
    latest_usage_payload: dict[str, Any] | None = None
    unknown_notification_counts: dict[str, int] = {}

    async def start_visible_tool(item: dict[str, Any], turn_id: str) -> str:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in tool_invocations:
            return item_id
        kind = str(item.get("type") or "tool")
        carrier_id = f"codex-tool:{turn_id}:{item_id}"
        await emit(
            "message.start",
            {"message_id": carrier_id, "role": "assistant", "content": ""},
        )
        name, arguments, _, _ = _tool_projection(item)
        raw_arguments = item.get("arguments")
        structured_arguments = (
            dict(raw_arguments) if isinstance(raw_arguments, dict) else {}
        )
        if (
            kind == "mcpToolCall"
            and requires_user_approval(
                name,
                structured_arguments,
                request.approval_mode,
            )
        ):
            mcp_item_correlator.register(
                name,
                structured_arguments,
                item_id,
            )
        invocation = start_tool_invocation(
            invocation_id=item_id,
            runtime_type="codex",
            name=name,
            arguments=arguments,
            mcp_catalog=invocation_catalog(item, name),
            native_kind=kind,
        )
        tool_invocations[item_id] = invocation
        await emit(
            "tool.start",
            {
                "message_id": carrier_id,
                "tool_call_id": item_id,
                "name": name,
                "arguments": json.dumps(
                    invocation[0].get("input"), ensure_ascii=False
                ),
                "invocation": invocation[0],
            },
        )
        # In the standard message protocol an assistant tool-call message is
        # complete before the matching tool response.
        await emit("message.end", {"message_id": carrier_id})
        return item_id

    result_ready = False
    try:
        phase_started = perf_counter()
        if (
            request.mcp_desired_state is None
            or request.mcp_execution_context is None
            or mcp_hub is None
            or mcp_adapter is None
        ):
            raise RuntimeError("Codex MCP Hub contracts are incomplete")
        mcp_adapter.set_gateway(request_mcp_gateway)
        await mcp_hub.reconcile(request.mcp_desired_state)
        await mcp_hub.activate(request.mcp_execution_context)
        active_hub_gateway = (
            hub_gateway_registry.get("aggregate")
            if hub_gateway_registry is not None
            else None
        )
        if active_hub_gateway is None:
            active_hub_gateway = CodexMcpHubGateway(mcp_hub, mcp_adapter)
            if hub_gateway_registry is not None:
                hub_gateway_registry["aggregate"] = active_hub_gateway
        runtime_mcp_catalog = await active_hub_gateway.activate(
            desired_servers=list(request.mcp_desired_state.servers),
            request_approval=request_platform_approval,
            requires_approval=lambda tool_name, arguments: (
                requires_user_approval(
                    tool_name,
                    arguments,
                    request.approval_mode,
                )
            ),
        )
        if active_hub_gateway.url is None:
            raise RuntimeError("Codex MCP Hub exposed no loopback URL")
        mcp_config = {
            "mcp_servers": {
                "skeinix": {
                    "url": active_hub_gateway.url,
                    "required": True,
                    "default_tools_approval_mode": "approve",
                    "tool_timeout_sec": _platform_mcp_tool_timeout_s(),
                }
            }
        }
        mcp_gateway_start_ms = int((perf_counter() - phase_started) * 1000)
        phase_started = perf_counter()
        mcp_config.update(broker_model_config)
        mcp_config_ms = int((perf_counter() - phase_started) * 1000)
        selected_model = request.model.get("id")
        common = {
            "cwd": "/data" if os.path.isdir("/data") else "/mount",
            "approvalPolicy": _approval_policy(request.approval_mode),
            "sandbox": "danger-full-access",
            "config": mcp_config,
        }
        if not account_mode:
            common["modelProvider"] = _BROKER_PROVIDER_ID
        if isinstance(selected_model, str) and selected_model:
            common["model"] = selected_model

        resident_config = json.dumps(
            {
                "cwd": common["cwd"],
                "config": common["config"],
                "modelProvider": common.get("modelProvider"),
                "mcpHubRevision": (
                    request.mcp_desired_state.revision_key
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )

        phase_started = perf_counter()
        resident_state_config = (
            resident_threads.get(request.runtime_state_ref)
            if request.runtime_state_ref and resident_threads is not None
            else None
        )
        reused_resident_thread = bool(
            request.runtime_state_ref
            and resident_state_config == resident_config
        )
        recovered_missing_rollout = False
        if (
            request.runtime_state_ref
            and resident_state_config == resident_config
        ):
            # The app-server already owns this live thread and its MCP clients.
            # Starting the next Turn directly avoids re-reading the native
            # transcript and re-running MCP startup on every user message.
            thread_id = request.runtime_state_ref
            thread = {"id": thread_id}
        elif request.runtime_state_ref and resident_state_config is not None:
            # A running app-server thread keeps the MCP clients it was opened
            # with. ``thread/resume`` rejoins that live thread, so newly
            # activated slash-command MCPs (for example /workflow after an
            # ordinary Turn) would not become model-visible. Forking copies the
            # completed conversation history into a new native thread while
            # applying the current Turn's exact, least-privilege MCP config.
            opened = await client.request(
                "thread/fork",
                {"threadId": request.runtime_state_ref, **common},
                timeout_s=45.0,
            )
            thread = opened.get("thread")
            thread_id = str(thread.get("id") if isinstance(thread, dict) else "")
            if thread_id and resident_threads is not None:
                resident_threads.pop(request.runtime_state_ref, None)
        else:
            if request.runtime_state_ref:
                try:
                    opened = await client.request(
                        "thread/resume",
                        {"threadId": request.runtime_state_ref, **common},
                        timeout_s=45.0,
                    )
                except CodexAppServerError as exc:
                    if not _missing_rollout_error(exc):
                        raise
                    recovered_missing_rollout = True
                    opened = await client.request(
                        "thread/start",
                        common,
                        timeout_s=45.0,
                    )
            else:
                opened = await client.request(
                    "thread/start", common, timeout_s=45.0
                )
            thread = opened.get("thread")
            thread_id = str(thread.get("id") if isinstance(thread, dict) else "")
        if not thread_id:
            raise RuntimeError("codex_thread_open_invalid_response")
        if resident_threads is not None:
            resident_threads[thread_id] = resident_config
        current["thread_id"] = thread_id
        thread_open_ms = int((perf_counter() - phase_started) * 1000)

        current_input = _turn_input(
            request,
            recovered_missing_rollout=recovered_missing_rollout,
        )
        if os.environ.get("AGENT_DEBUG_VIEW_ENABLED") == "1":
            # Build/write concurrently with app-server turn startup so the
            # Inspector adds no model TTFT. The task is drained before the
            # Runtime result, which guarantees workspace writeback observes it.
            debug_snapshot_task = asyncio.create_task(asyncio.to_thread(
                capture_codex_debug_snapshot,
                request=request,
                thread=thread if isinstance(thread, dict) else {},
                thread_id=thread_id,
                current_input=current_input,
            ))

        client_user_message_id = f"{request.chat_id}:user:{request.turn_id}"
        if request.continuation_index:
            client_user_message_id += (
                f":continuation:{request.continuation_index}"
            )
        turn_params: dict[str, Any] = {
            "threadId": thread_id,
            "input": current_input,
            "clientUserMessageId": client_user_message_id,
            "approvalPolicy": _approval_policy(request.approval_mode),
        }
        if isinstance(selected_model, str) and selected_model:
            turn_params["model"] = selected_model
        if request.reasoning_effort:
            turn_params["effort"] = request.reasoning_effort
        phase_started = perf_counter()
        started = await client.request("turn/start", turn_params, timeout_s=45.0)
        turn = started.get("turn")
        turn_id = str(turn.get("id") if isinstance(turn, dict) else "")
        if not turn_id:
            raise RuntimeError("codex_turn_start_invalid_response")
        current["turn_id"] = turn_id
        turn_start_ms = int((perf_counter() - phase_started) * 1000)
        await emit(
            "runtime.started",
            {
                # A state reference may be provisioned before the first user
                # Turn.  The backend-owned command snapshot is therefore the
                # authoritative product first/subsequent-Turn classification.
                "first_turn": bool(request.command_context.is_first),
                "mcp_server_count": (
                    len(request.mcp_desired_state.servers)
                ),
                "timings_ms": {
                    "skills_prepare_ms": skills_prepare_ms,
                    "app_server_start_ms": app_server_start_ms,
                    "mcp_gateway_start_ms": mcp_gateway_start_ms,
                    "mcp_config_ms": mcp_config_ms,
                    "thread_open_ms": thread_open_ms,
                    "turn_start_ms": turn_start_ms,
                    "setup_total_ms": int(
                        (perf_counter() - setup_started) * 1000
                    ),
                },
            },
        )
        checkpoint_payload = {"state_ref": thread_id}
        if (
            request.runtime_state_ref
            and request.runtime_state_ref != thread_id
        ):
            checkpoint_payload["previous_state_ref"] = request.runtime_state_ref
        await emit("checkpoint", checkpoint_payload)

        async for message in client.messages():
            method = str(message.get("method") or "")
            params = message.get("params")
            params = params if isinstance(params, dict) else {}

            if "id" in message and method in _CODEX_INTERACTIVE_SERVER_REQUESTS:
                native_request_id = message["id"]
                native_item_id = str(
                    params.get("itemId") or f"request-input:{native_request_id}"
                )
                await start_visible_tool(
                    {
                        "id": native_item_id,
                        "type": "dynamicToolCall",
                        "tool": (
                            "request_user_input"
                            if method == "item/tool/requestUserInput"
                            else "mcp_elicitation"
                        ),
                        "arguments": {
                            "question_count": len(params.get("questions") or []),
                            "server": params.get("serverName"),
                            "mode": params.get("mode"),
                        },
                    },
                    turn_id,
                )
                interaction_seed = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    ":".join(
                        (
                            "vibecanvas",
                            "codex-interaction",
                            thread_id,
                            turn_id,
                            str(native_request_id),
                            method,
                        )
                    ),
                ).hex
                hitl_id = f"hitl_{interaction_seed[:16]}"
                artifact_id = f"ia_{interaction_seed[:16]}"
                definition = _interaction_definition(
                    method,
                    params,
                    artifact_id=artifact_id,
                    hitl_request_id=hitl_id,
                )
                correlation = {
                    "source": "codex_app_server",
                    "runtime_request_id": native_request_id,
                    "runtime_method": method,
                    "runtime_thread_id": thread_id,
                    "runtime_turn_id": turn_id,
                    "runtime_item_id": native_item_id,
                }
                await emit(
                    "interaction.required",
                    {
                        "hitl_request_id": hitl_id,
                        "hitl_type": "elicitation",
                        "title": definition["title"],
                        "prompt_text": definition["props"]["message"],
                        "artifact_id": artifact_id,
                        "tool_call_id": native_item_id,
                        "interaction_definition": definition,
                        "resume_mode": "same_turn",
                        "agent_payload": {
                            "method": method,
                            "awaiting_user_input": True,
                        },
                        "runtime_correlation": correlation,
                    },
                )
                response = await control_router.wait(
                    "codex_app_server", native_request_id
                )
                await client.respond(
                    native_request_id,
                    _interaction_response(method, params, response),
                )
                prior_invocation = tool_invocations.pop(native_item_id, None)
                action = str(response.get("action") or "cancel")
                await emit(
                    "tool.end",
                    {
                        "tool_call_id": native_item_id,
                        "name": (
                            "request_user_input"
                            if method == "item/tool/requestUserInput"
                            else "mcp_elicitation"
                        ),
                        # Cancelling an input prompt is a successful control
                        # decision, not a failed tool execution. The richer
                        # invocation envelope still retains `cancelled`.
                        "status": "done",
                        "content": (
                            "User input submitted"
                            if action == "submit"
                            else "User input cancelled"
                        ),
                        "invocation": finish_tool_invocation(
                            prior_invocation[0] if prior_invocation else None,
                            started_monotonic=(
                                prior_invocation[1] if prior_invocation else None
                            ),
                            invocation_id=native_item_id,
                            runtime_type="codex",
                            name=(
                                "request_user_input"
                                if method == "item/tool/requestUserInput"
                                else "mcp_elicitation"
                            ),
                            status=(
                                "done" if action == "submit" else "cancelled"
                            ),
                            content=(
                                "User input submitted"
                                if action == "submit"
                                else "User input cancelled"
                            ),
                            artifact=None,
                            native_kind=method,
                        ),
                    },
                )
                if bool(response.get("persisted")):
                    await emit(
                        "interaction.resolved",
                        {
                            "hitl_request_id": hitl_id,
                            "status": "submitted" if action == "submit" else "cancelled",
                        },
                    )
                continue

            if "id" in message and method in _CODEX_APPROVAL_SERVER_REQUESTS:
                native_request_id = message["id"]
                title, prompt = _approval_prompt(method, params)
                approval_seed = uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    ":".join(
                        (
                            "vibecanvas",
                            "codex-approval",
                            thread_id,
                            turn_id,
                            str(params.get("itemId") or ""),
                            method,
                        )
                    ),
                ).hex
                hitl_id = f"hitl_{approval_seed[:16]}"
                correlation = {
                    "source": "codex_app_server",
                    "runtime_request_id": native_request_id,
                    "runtime_method": method,
                    "runtime_thread_id": thread_id,
                    "runtime_turn_id": turn_id,
                    "runtime_item_id": params.get("itemId"),
                    "runtime_approval_id": params.get("approvalId"),
                }
                await emit(
                    "approval.requested",
                    {
                        "hitl_request_id": hitl_id,
                        "hitl_type": "pre_tool_approval",
                        "title": title,
                        "prompt_text": prompt,
                        "actions": [
                            {"id": "approve", "label": "Approve", "variant": "primary"},
                            {"id": "deny", "label": "Deny", "variant": "secondary"},
                        ],
                        "agent_payload": {
                            "method": method,
                            "tool": (
                                "shell"
                                if method == "item/commandExecution/requestApproval"
                                else (
                                    "permissions"
                                    if method == "item/permissions/requestApproval"
                                    else "file_change"
                                )
                            ),
                            "arguments": params,
                            "item_id": params.get("itemId"),
                            "reason": params.get("reason"),
                        },
                        "policy": {
                            "phase": "pre_tool",
                            "native_required": True,
                        },
                        "runtime_correlation": correlation,
                    },
                )
                response = await control_router.wait(
                    "codex_app_server", native_request_id
                )
                action = str(response.get("action") or "deny")
                native_response = _approval_response(method, params, action)
                await client.respond(native_request_id, native_response)
                if bool(response.get("persisted")):
                    await emit(
                        "approval.resolved",
                        {
                            "hitl_request_id": hitl_id,
                            "status": {
                                "approve": "approved",
                                "deny": "denied",
                                "cancel": "cancelled",
                            }.get(action, "denied"),
                        },
                    )
                continue

            if "id" in message:
                # A newly introduced server request must never leave the
                # app-server blocked forever. Close it with a bounded JSON-RPC
                # error and expose only a sanitized upgrade warning.
                native_request_id = message["id"]
                known_unsupported = method in _CODEX_REJECTED_SERVER_REQUESTS
                await client.respond_error(
                    native_request_id,
                    code=-32601,
                    message="This Codex request is not supported by Skeinix yet.",
                )
                await emit(
                    "projection",
                    {
                        "event_type": "NOTICE",
                        "payload": {
                            "level": "warning",
                            "code": (
                                "codex_request_unsupported"
                                if known_unsupported
                                else "codex_request_unknown"
                            ),
                            "message": (
                                "Codex requested an unsupported interaction; "
                                "the request was closed instead of waiting indefinitely."
                            ),
                            "runtime_type": "codex",
                            "native_kind": method[:160],
                            "turn_disposition": "continue",
                        },
                    },
                )
                continue

            if method in {"hook/started", "hook/completed"}:
                run = params.get("run")
                run = run if isinstance(run, dict) else {}
                run_id = str(run.get("id") or "")
                native_item_id = f"hook:{run_id}" if run_id else ""
                if native_item_id:
                    hook_item = {
                        "id": native_item_id,
                        "type": "hookPrompt",
                        "run": {
                            "eventName": run.get("eventName"),
                            "scope": run.get("scope"),
                            "executionMode": run.get("executionMode"),
                            "status": run.get("status"),
                            "statusMessage": run.get("statusMessage"),
                        },
                        "status": run.get("status"),
                    }
                    await start_visible_tool(hook_item, turn_id)
                    if method == "hook/completed":
                        name, _, output, _ = _tool_projection(hook_item)
                        status = _tool_completion_status(hook_item)
                        prior_invocation = tool_invocations.pop(
                            native_item_id, None
                        )
                        await emit(
                            "tool.end",
                            {
                                "tool_call_id": native_item_id,
                                "name": name,
                                "status": status,
                                "content": output,
                                "invocation": finish_tool_invocation(
                                    (
                                        prior_invocation[0]
                                        if prior_invocation
                                        else None
                                    ),
                                    started_monotonic=(
                                        prior_invocation[1]
                                        if prior_invocation
                                        else None
                                    ),
                                    invocation_id=native_item_id,
                                    runtime_type="codex",
                                    name=name,
                                    status=status,
                                    content=output,
                                    artifact=None,
                                    native_kind=method,
                                ),
                            },
                        )
                continue

            if method == "mcpServer/startupStatus/updated":
                server_name = str(params.get("name") or "MCP server")[:100]
                native_status = str(params.get("status") or "starting")
                # Codex may re-announce the already-live MCP clients at the
                # beginning of every turn. Keep failures visible, but do not
                # replay successful startup cards when this turn reused the
                # same resident thread and unchanged MCP configuration.
                if reused_resident_thread and native_status not in {
                    "failed",
                    "cancelled",
                }:
                    continue
                native_item_id = "mcp-startup:" + uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"vibecanvas:codex-mcp-startup:{thread_id}:{server_name}",
                ).hex[:16]
                item = {
                    "id": native_item_id,
                    "type": "mcpToolCall",
                    "server": server_name,
                    "tool": "mcp_startup",
                    "arguments": {"server": server_name},
                    "status": {
                        "ready": "completed",
                        "failed": "failed",
                        "cancelled": "cancelled",
                    }.get(native_status, "inProgress"),
                    "result": {
                        "server": server_name,
                        "status": native_status,
                        "failure_reason": str(
                            params.get("failureReason") or ""
                        )[:100],
                    },
                }
                await start_visible_tool(item, turn_id)
                if native_status in {"ready", "failed", "cancelled"}:
                    name, _, output, artifact = _tool_projection(item)
                    status = _tool_completion_status(item)
                    prior_invocation = tool_invocations.pop(
                        native_item_id, None
                    )
                    await emit(
                        "tool.end",
                        {
                            "tool_call_id": native_item_id,
                            "name": name,
                            "status": status,
                            "content": output,
                            "invocation": finish_tool_invocation(
                                (
                                    prior_invocation[0]
                                    if prior_invocation
                                    else None
                                ),
                                started_monotonic=(
                                    prior_invocation[1]
                                    if prior_invocation
                                    else None
                                ),
                                invocation_id=native_item_id,
                                runtime_type="codex",
                                name=name,
                                status=status,
                                content=output,
                                artifact=artifact,
                                mcp_catalog=invocation_catalog(item, name),
                                native_kind=method,
                            ),
                        },
                    )
                continue

            notice = _safe_codex_notice(method, params)
            if notice is not None:
                await emit(
                    "projection",
                    {"event_type": "NOTICE", "payload": notice},
                )
                continue

            if method == "thread/tokenUsage/updated":
                usage = params.get("tokenUsage")
                usage = usage if isinstance(usage, dict) else {}
                last = usage.get("last")
                last = last if isinstance(last, dict) else {}
                latest_usage_payload = {
                    "model": str(selected_model or "unknown"),
                    "prompt_tokens": int(last.get("inputTokens") or 0),
                    "completion_tokens": int(last.get("outputTokens") or 0),
                    "cached_input_tokens": int(
                        last.get("cachedInputTokens") or 0
                    ),
                    "reasoning_output_tokens": int(
                        last.get("reasoningOutputTokens") or 0
                    ),
                    "total_tokens": int(last.get("totalTokens") or 0),
                    "context_window_tokens": (
                        int(usage["modelContextWindow"])
                        if isinstance(usage.get("modelContextWindow"), int)
                        else None
                    ),
                    "native_kind": method,
                }
                continue

            if method == "item/started":
                item = params.get("item")
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                kind = item.get("type")
                if kind == "agentMessage" and item_id:
                    open_messages.add(item_id)
                    await emit(
                        "message.start",
                        {"message_id": item_id, "role": "assistant", "content": ""},
                    )
                elif kind in _VISIBLE_TOOL_ITEM_KINDS and item_id:
                    await start_visible_tool(item, turn_id)
                continue

            if method == "item/agentMessage/delta":
                item_id = str(params.get("itemId") or "")
                delta = str(params.get("delta") or "")
                if item_id and delta:
                    if item_id not in open_messages:
                        open_messages.add(item_id)
                        await emit(
                            "message.start",
                            {"message_id": item_id, "role": "assistant", "content": ""},
                        )
                    message_had_delta.add(item_id)
                    await emit("message.delta", {"message_id": item_id, "delta": delta})
                continue

            if method in {
                "item/commandExecution/outputDelta",
                "item/fileChange/outputDelta",
                "item/mcpToolCall/progress",
                "item/reasoning/summaryTextDelta",
            }:
                item_id = str(params.get("itemId") or "")
                delta = params.get("delta") or params.get("message") or ""
                if item_id and delta:
                    await emit(
                        "tool.update",
                        {"tool_call_id": item_id, "content": str(delta)},
                    )
                continue

            if method == "item/fileChange/patchUpdated":
                item_id = str(params.get("itemId") or "")
                if item_id:
                    await emit(
                        "tool.update",
                        {
                            "tool_call_id": item_id,
                            "content": _file_change_progress(params),
                        },
                    )
                continue

            if method == "turn/plan/updated":
                # Codex publishes the authoritative full plan snapshot:
                # {step, status: pending|inProgress|completed}. It deliberately
                # has no stable item id, so the platform assigns deterministic
                # one-based ids from snapshot order and never consumes the
                # experimental item/plan/delta stream.
                normalized = _normalize_codex_plan(params.get("plan"))
                await emit(
                    "projection",
                    {
                        "event_type": "CHAT_EVENT",
                        "payload": {
                            "type": "todo_update",
                            "items": normalized,
                        },
                    },
                )
                continue

            if method == "item/completed":
                item = params.get("item")
                if not isinstance(item, dict):
                    continue
                item_id = str(item.get("id") or "")
                kind = item.get("type")
                if kind == "agentMessage" and item_id:
                    text = str(item.get("text") or "")
                    if text and item_id not in message_had_delta:
                        await emit(
                            "message.delta", {"message_id": item_id, "delta": text}
                        )
                    await emit("message.end", {"message_id": item_id})
                    open_messages.discard(item_id)
                elif kind in _VISIBLE_TOOL_ITEM_KINDS and item_id:
                    name, _, output, artifact = _tool_projection(item)
                    status = _tool_completion_status(item)
                    prior_invocation = tool_invocations.pop(item_id, None)
                    interaction_payload: dict[str, Any] | None = None
                    if artifact is not None:
                        payload = artifact.get("payload")
                        payload = payload if isinstance(payload, dict) else {}
                        definition = payload.get("artifact")
                        definition = (
                            definition if isinstance(definition, dict) else {}
                        )
                        if definition.get("completion_mode") == "wait_for_submit":
                            artifact_id = str(
                                definition.get("artifact_id")
                                or payload.get("artifact_id")
                                or ""
                            )
                            if not artifact_id:
                                raise RuntimeError(
                                    "Codex render_interactive result is missing artifact_id"
                                )
                            hitl_seed = uuid.uuid5(
                                uuid.NAMESPACE_URL,
                                f"vibecanvas:interactive:{artifact_id}",
                            ).hex[:16]
                            hitl_request_id = f"hitl_{hitl_seed}"
                            definition["hitl_request_id"] = hitl_request_id
                            interaction_state = definition.get("interaction_state")
                            if isinstance(interaction_state, dict):
                                interaction_state["status"] = "pending"
                            payload["hitl_request_id"] = hitl_request_id
                            interaction_schema = definition.get("interaction_schema")
                            interaction_schema = (
                                interaction_schema
                                if isinstance(interaction_schema, dict)
                                else {}
                            )
                            continue_only = bool(
                                definition.get("require_human_confirm")
                                or interaction_schema.get("interaction_type")
                                == "continue"
                            )
                            interaction_payload = {
                                "hitl_request_id": hitl_request_id,
                                "hitl_type": (
                                    "post_tool_review"
                                    if continue_only
                                    else "elicitation"
                                ),
                                "title": str(
                                    definition.get("title")
                                    or "Interactive review"
                                ),
                                "prompt_text": (
                                    "Review the interactive content, then click Continue."
                                    if continue_only
                                    else "Review the interactive content and submit or cancel before continuing."
                                ),
                                "artifact_id": artifact_id,
                                "tool_call_id": item_id,
                                "artifact": artifact,
                                "agent_payload": {
                                    "tool": "render_interactive",
                                    "artifact_id": artifact_id,
                                    "resume_mode": "new_turn",
                                    "interaction_type": (
                                        "continue" if continue_only else "input"
                                    ),
                                },
                                "runtime_correlation": {
                                    "source": "codex_app_server",
                                    "runtime_request_id": artifact_id,
                                    "runtime_method": "item/completed",
                                    "runtime_thread_id": thread_id,
                                    "runtime_turn_id": turn_id,
                                    "runtime_item_id": item_id,
                                },
                            }
                            # The backend consumes and persists this event before
                            # exposing the Tool result card.
                            await emit("interaction.required", interaction_payload)
                    await emit(
                        "tool.end",
                        {
                            "tool_call_id": item_id,
                            "name": name,
                            "status": status,
                            "content": output,
                            "invocation": finish_tool_invocation(
                                prior_invocation[0] if prior_invocation else None,
                                started_monotonic=(prior_invocation[1] if prior_invocation else None),
                                invocation_id=item_id,
                                runtime_type="codex",
                                name=name,
                                status=status,
                                content=output,
                                artifact=artifact,
                                mcp_catalog=invocation_catalog(item, name),
                                native_kind=str(kind),
                            ),
                            **({"artifact": artifact} if artifact is not None else {}),
                        },
                    )
                    if interaction_payload is not None:
                        # This is a completed tool boundary, not a suspended
                        # app-server request. Close the current Codex turn; the
                        # user's Continue becomes an ordinary new Human Turn.
                        try:
                            await client.request(
                                "turn/interrupt",
                                {"threadId": thread_id, "turnId": turn_id},
                                timeout_s=10.0,
                            )
                        except Exception:
                            pass
                        break
                continue

            if method == "turn/completed":
                completed = params.get("turn")
                if not isinstance(completed, dict) or str(completed.get("id") or "") != turn_id:
                    continue
                status = str(completed.get("status") or "")
                if status in {"failed", "errored"}:
                    error = completed.get("error")
                    raise RuntimeError(
                        str(error.get("message") if isinstance(error, dict) else error)
                        or "Codex turn failed"
                    )
                break

            if method in _CODEX_RECOGNIZED_NOTIFICATIONS:
                continue

            if method:
                count = unknown_notification_counts.get(method, 0) + 1
                unknown_notification_counts[method] = count
                # Log a bounded schema-upgrade signal without raw params. The
                # first occurrence is also visible as a non-terminal warning.
                if count == 1 and len(unknown_notification_counts) <= 8:
                    await emit(
                        "projection",
                        {
                            "event_type": "NOTICE",
                            "payload": {
                                "level": "warning",
                                "code": "codex_event_unsupported",
                                "message": (
                                    "Codex emitted a newer event that is not "
                                    "fully displayed yet. The Turn can continue."
                                ),
                                "runtime_type": "codex",
                                "native_kind": method[:160],
                                "occurrence_count": count,
                                "turn_disposition": "continue",
                            },
                        },
                    )

        for message_id in list(open_messages):
            await emit("message.end", {"message_id": message_id})
        await finish_debug_snapshot()
        if latest_usage_payload is not None:
            await emit("usage", latest_usage_payload)
        if unknown_notification_counts:
            bounded_counts = dict(
                list(sorted(unknown_notification_counts.items()))[:8]
            )
            print(
                "⚠️  [codex] unsupported notification counts="
                + json.dumps(bounded_counts, ensure_ascii=True)
            )
        await emit("runtime.completed", {"state_ref": thread_id})
        result_ready = True
    except CodexAppServerError as exc:
        raise RuntimeError(f"{exc.code}: {exc}") from exc
    finally:
        if model_capability is not None:
            _remove_broker_capability()
            _remove_forbidden_account_cache(request.runtime_root)
        await finish_debug_snapshot()
        control_router.cancel()
        mcp_item_correlator.cancel()
        control_task.cancel()
        await asyncio.gather(control_task, return_exceptions=True)
        if active_hub_gateway is not None:
            active_hub_gateway.deactivate()
        if mcp_hub is not None:
            await mcp_hub.deactivate()
        if close_client:
            await client.close()
    if result_ready:
        # Do not advertise a reusable Turn boundary while the old control
        # receiver or MCP upstream is still being dismantled.  The outer
        # sandbox loop may safely receive the next runtime_request immediately
        # after this frame.
        from vibecanvas_engine.sandbox_bus import MSG_RUNTIME_RESULT

        await channel.send({"type": MSG_RUNTIME_RESULT})


__all__ = ["create_codex_app_server", "run_codex_turn"]
