"""Agent Runtime entrypoint executed inside the user sandbox.

The host sends stable ``RuntimeTurnRequest`` objects over the private UDS bus.
The process stays resident for consecutive turns in the same Chat, so gVisor
boot and Python module imports are paid once. Provider/SDK objects never cross
the boundary.
"""

# Imports below ``_append_extra_python_paths`` intentionally depend on paths
# injected by the sandbox launcher.
# ruff: noqa: E402

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from contextlib import suppress


def _append_extra_python_paths() -> None:
    for path in os.environ.get("VC_SANDBOX_PYTHON_PATHS", "").split(os.pathsep):
        if path and path not in sys.path:
            sys.path.append(path)


_append_extra_python_paths()


from vibecanvas_engine.sandbox_bus import (
    MSG_BACKGROUND_JOB_EVENT,
    MSG_BACKGROUND_JOB_REQUEST,
    MSG_BACKGROUND_JOB_RESULT,
    MSG_RUNTIME_CONTROL,
    MSG_RUNTIME_ERROR,
    MSG_RUNTIME_EVENT,
    MSG_RUNTIME_REQUEST,
    MSG_RUNTIME_RESULT,
    MSG_RUNTIME_STATE_RESPONSE,
    connect_bus,
)

from vibecanvas_api.services.agent_runtime.control import RuntimeControlRouter
from vibecanvas_api.services.agent_runtime.protocol import (
    RuntimeBackgroundJobRequest,
    RuntimeEvent,
    RuntimeTurnRequest,
    RuntimeType,
)
from vibecanvas_api.services.agent_runtime.state_client import (
    BrokerCheckpointSaver,
    RuntimeStateRpcClient,
)


def _command_instruction_projection(
    request: RuntimeTurnRequest,
) -> tuple[dict[str, str], set[str]]:
    """Project backend instructions into LangChain-native edit inputs."""

    contexts = {
        instruction.name: instruction.content
        for instruction in request.instructions
        if instruction.kind == "command_context"
    }
    activated = {
        instruction.name
        for instruction in request.instructions
        if instruction.kind == "command_context"
        and instruction.activated_this_turn
    }
    if request.runtime_state_ref is None:
        activated.update(contexts)
    return contexts, activated


_codex_client = None
_codex_gateways = {}
_codex_threads = {}


def _preload_runtime(runtime_type: str) -> None:
    """Import the credential-free Runtime graph before accepting a Chat.

    Rootful sandboxd checkpoints the process immediately after this boundary.
    The image contains platform code and imported SDK modules only: no request,
    tenant identifier, model capability, VFS content, account token, MCP
    authorization, or connected network socket has entered the process yet.
    """

    if runtime_type == RuntimeType.LANGCHAIN.value:
        from vibecanvas_api import agent as _agent  # noqa: F401
        from vibecanvas_api import context as _context  # noqa: F401
        from vibecanvas_api.services.agent_runtime import (  # noqa: F401
            filesystem_vfs as _filesystem_vfs,
        )
        from vibecanvas_api.services.agent_runtime import (
            mcp as _mcp,
        )
        _ = (_agent, _context, _filesystem_vfs, _mcp)
        return
    if runtime_type == RuntimeType.CODEX.value:
        from vibecanvas_api.services.agent_runtime import codex as _codex  # noqa: F401
        return
    raise ValueError(f"unsupported Runtime bootstrap type: {runtime_type}")


async def _wait_for_bus_socket(socket_path: str) -> None:
    """Wait at the clean snapshot boundary until a Chat broker is mounted."""

    while not os.path.exists(socket_path):
        await asyncio.sleep(0.02)


async def _close_codex_runtime_resources() -> None:
    global _codex_client, _codex_gateways, _codex_threads
    client = _codex_client
    gateways = _codex_gateways
    _codex_client = None
    _codex_gateways = {}
    _codex_threads = {}
    if client is not None:
        with suppress(Exception):
            await client.close()
    for gateway in reversed(list(gateways.values())):
        with suppress(Exception):
            await gateway.close()


async def _control_loop(
    channel,
    stop_event: asyncio.Event,
    owner_task: asyncio.Task | None,
    control_router: RuntimeControlRouter,
    state_client: RuntimeStateRpcClient,
) -> None:
    while True:
        message = await channel.recv()
        if message is None:
            stop_event.set()
            state_client.fail_all()
            return
        if message.get("type") == MSG_RUNTIME_STATE_RESPONSE:
            response = message.get("response")
            if isinstance(response, dict):
                state_client.deliver(response)
            continue
        if message.get("type") != MSG_RUNTIME_CONTROL:
            continue
        control = message.get("response") or {}
        correlation = control.get("correlation")
        if control.get("action") == "cancel" and not correlation:
            stop_event.set()
            # Interrupt the current model/tool await at the Runtime boundary.
            # run_agent_turn observes stop_event while handling cancellation and
            # emits protocol closure for any open message/tool before returning.
            if owner_task is not None and not owner_task.done():
                owner_task.cancel()
            return
        control_router.deliver(control)


async def _emit(channel, event: RuntimeEvent) -> None:
    await channel.send(
        {"type": MSG_RUNTIME_EVENT, "event": event.model_dump(mode="json")}
    )


async def _run_langchain(channel, request: RuntimeTurnRequest) -> None:
    # Keep the unified sandbox entrypoint cheap for other Runtime adapters.
    # Importing LangGraph, database drivers, the full Agent tool graph, and MCP
    # adapters before dispatch made a Codex-only process pay LangChain's entire
    # startup cost and could exceed the host's bus-connect deadline.
    from vibecanvas_api.agent import run_agent_turn
    from vibecanvas_api.context import build_signal, clear_stores, init_stores
    from vibecanvas_api.services.agent_runtime.filesystem_vfs import (
        FilesystemRuntimeVfsStore,
    )
    from vibecanvas_api.services.agent_runtime.mcp import load_runtime_mcp_tools

    if request.runtime_type != RuntimeType.LANGCHAIN:
        raise ValueError(f"unsupported runtime type: {request.runtime_type.value}")

    tenant_id = str(request.tenant_id)
    os.environ["VIBECANVAS_AGENT_RUNTIME_IN_SANDBOX"] = "1"
    state_client = RuntimeStateRpcClient(channel)
    checkpointer = BrokerCheckpointSaver(state_client)

    stop_event = asyncio.Event()
    control_router = RuntimeControlRouter()
    control_task = asyncio.create_task(
        _control_loop(
            channel,
            stop_event,
            asyncio.current_task(),
            control_router,
            state_client,
        )
    )
    seq = 1

    def event(event_type: str, payload: dict) -> RuntimeEvent:
        nonlocal seq
        result = RuntimeEvent(
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
        return result

    async def request_tool_approval(
        tool_name: str,
        tool_call_id: str,
        arguments: dict,
    ) -> str:
        """Suspend after argument generation and before the tool handler."""
        if not tool_call_id:
            return "denied"
        approval_seed = uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                "vibecanvas:langchain-tool-approval:"
                f"{request.turn_id}:{tool_call_id}"
            ),
        ).hex
        hitl_request_id = f"hitl_{approval_seed[:16]}"
        prompt_text = str(
            arguments.get("approval_reason")
            or arguments.get("purpose")
            or f"Allow the agent to execute {tool_name}?"
        )
        correlation = {
            "source": "langchain",
            "runtime_request_id": tool_call_id,
            "runtime_method": "tool/approval",
            "runtime_thread_id": request.runtime_state_ref,
            "runtime_turn_id": request.turn_id,
            "runtime_item_id": tool_call_id,
        }
        waiter = asyncio.create_task(
            control_router.wait("langchain", tool_call_id)
        )
        try:
            await _emit(
                channel,
                event(
                    "approval.requested",
                    {
                        "hitl_request_id": hitl_request_id,
                        "hitl_type": "pre_tool_approval",
                        "title": f"Approve {tool_name}",
                        "prompt_text": prompt_text,
                        "actions": [
                            {
                                "id": "approve",
                                "label": "Approve",
                                "variant": "primary",
                            },
                            {
                                "id": "deny",
                                "label": "Deny",
                                "variant": "secondary",
                            },
                        ],
                        "agent_payload": {
                            "tool": tool_name,
                            "arguments": arguments,
                        },
                        "policy": {
                            "phase": "pre_tool",
                            "native_required": False,
                        },
                        "runtime_correlation": correlation,
                    },
                ),
            )
            response = await waiter
            action = str(response.get("action") or "deny")
            status = {
                "approve": "approved",
                "deny": "denied",
                "cancel": "cancelled",
            }.get(action, "denied")
            if bool(response.get("persisted")):
                await _emit(
                    channel,
                    event(
                        "approval.resolved",
                        {
                            "hitl_request_id": hitl_request_id,
                            "status": status,
                        },
                    ),
                )
            return status
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def request_background_job(
        tool_call_id: str,
        job_spec: dict,
    ) -> dict:
        """Run one LangChain-private background control operation on the host."""
        if not tool_call_id:
            return {
                "action": "rejected",
                "error": "background job requires a tool_call_id",
            }
        operation = str((job_spec or {}).get("operation") or "submit")
        if operation not in {"submit", "list", "cancel"}:
            return {
                "action": "rejected",
                "error": f"unsupported background job operation: {operation}",
            }
        request_id = (
            "bgreq_"
            + uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{request.turn_id}:{tool_call_id}:{operation}",
            ).hex[:16]
        )
        correlation = {
            "source": "langchain_background",
            "runtime_request_id": tool_call_id,
            "runtime_method": f"background_job/{operation}",
            "runtime_thread_id": request.runtime_state_ref,
            "runtime_turn_id": request.turn_id,
            "runtime_item_id": tool_call_id,
        }
        waiter = asyncio.create_task(
            control_router.wait("langchain_background", tool_call_id)
        )
        try:
            await _emit(
                channel,
                event(
                    "background_job.requested",
                    {
                        "request_id": request_id,
                        "operation": operation,
                        "job": dict(job_spec or {}),
                        # Secret-bearing model configuration is private bus
                        # material. The host must never persist or project it.
                        "execution_private": {
                            "model": dict(request.model or {}),
                        },
                        "runtime_correlation": correlation,
                    },
                ),
            )
            return await waiter
        finally:
            if not waiter.done():
                waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def publish_post_tool_interaction(payload: dict) -> None:
        """Hand durable HITL ownership to the host before Tool projection."""
        await _emit(channel, event("interaction.required", dict(payload)))

    async def publish_runtime_usage(payload: dict) -> None:
        """Publish metering facts without granting the sandbox database access."""
        await _emit(channel, event("usage", dict(payload)))

    completed = False
    try:
        await _emit(channel, event("runtime.started", {}))
        # Runtime-private state is created by the adapter and never projected
        # through VFS/user APIs. Ordinary Agent filesystem tools deliberately
        # exclude /runtime from their root allowlist.
        if os.path.isdir("/runtime"):
            os.makedirs(request.runtime_root, mode=0o700, exist_ok=True)
        command = request.command_context.model_dump()
        user_message = dict(request.message or {})
        active_modes = set(command.get("active_modes") or [])
        command_contexts, activated_commands = _command_instruction_projection(
            request
        )
        attached_platform_mcps = [
            server.name for server in request.mcp_servers if server.source == "platform"
        ]
        expected_platform_mcps = set(request.active_platform_mcps)
        if set(attached_platform_mcps) != expected_platform_mcps:
            raise RuntimeError(
                "Platform MCP descriptors do not match active capabilities"
            )
        # build/browser are activated through platform MCP descriptors.  Until a
        # descriptor is attached, never fall back to the legacy direct tool set.
        active_modes.difference_update({"build", "browser"})

        # Workspace data is already hydrated into Chat-scoped mounts by the
        # host.  Middleware reads/writes those files directly; the host performs
        # durable VFS writeback once the turn is quiescent.
        vfs_store = FilesystemRuntimeVfsStore()
        runtime_mcp_tools, runtime_mcp_catalog = await load_runtime_mcp_tools(
            request.mcp_servers
        )
        # Tools such as ``subagent`` resolve Runtime-local services through the
        # legacy process context. Both state and files are sandbox-safe adapters;
        # neither carries a platform or checkpoint database credential.
        init_stores(checkpointer, vfs_store)

        async for signal in run_agent_turn(
            user_message=user_message,
            thread_id=(
                request.runtime_state_ref
                or str(command.get("thread_id") or request.runtime_session_id)
            ),
            is_first=bool(command.get("is_first", False)),
            # Workflow graph data never crosses the backend↔Runtime protocol.
            # Privileged workflow tools resolve the Chat's durable binding and
            # current graph through Platform MCP for each call.
            workflow={},
            chat_context=str(command.get("chat_context") or ""),
            agent_cfg=dict(request.model or {}),
            checkpointer=checkpointer,
            build_signal=build_signal,
            chat_id=request.chat_id,
            stop_event=stop_event,
            execution_context="",
            attachments=list(request.attachments or []),
            repo=None,
            vfs_store=vfs_store,
            username=request.user_id,
            wf_id=str(command.get("workspace_scope_id") or request.chat_id),
            current_workflow_id=command.get("current_workflow_id"),
            tenant_id=tenant_id,
            surface=str(command.get("agent_surface") or "chat"),
            approval_mode=request.approval_mode,
            available_commands=set(command.get("available_commands") or []),
            active_modes=active_modes,
            command_contexts=command_contexts,
            activated_this_turn=activated_commands,
            turn_id=request.turn_id,
            runtime_mcp_tools=runtime_mcp_tools,
            runtime_mcp_catalog=runtime_mcp_catalog,
            runtime_skill_catalog=[
                skill.model_dump(mode="json") for skill in request.skills
            ],
            runtime_todo_items=list(request.todo_items),
            runtime_interactive_artifact_refs=dict(
                request.interactive_artifact_refs
            ),
            runtime_context_manifest=request.context_manifest.model_dump(mode="json"),
            conversation_clock=(
                request.conversation_clock.model_dump(mode="json")
                if request.conversation_clock is not None
                else None
            ),
            request_tool_approval=request_tool_approval,
            publish_post_tool_interaction=publish_post_tool_interaction,
            publish_runtime_usage=publish_runtime_usage,
            request_background_job=request_background_job,
        ):
            payload = {
                "event_type": signal.get("type"),
                "payload": signal.get("payload") or {},
                "signal_id": signal.get("__signal_id__"),
            }
            await _emit(channel, event("projection", payload))

        await _emit(channel, event("runtime.completed", {}))
        completed = True
    finally:
        clear_stores(expected_checkpointer=checkpointer)
        state_client.fail_all("runtime turn completed")
        control_task.cancel()
        await asyncio.gather(control_task, return_exceptions=True)
    if completed:
        # The terminal boundary means the resident process is ready to receive
        # its next Turn, not merely that model output has ended.  Publish it
        # only after the Turn-local channel reader has fully stopped.
        await channel.send({"type": MSG_RUNTIME_RESULT})


async def _run_background_subagent(
    channel,
    request: RuntimeBackgroundJobRequest,
) -> None:
    """Execute one LangChain subagent in a process independent of its parent Turn."""
    from vibecanvas_api.agent import AgentContext, _build_chat_model
    from vibecanvas_api.agents.tools.subagent.core import run_bounded_agent
    from vibecanvas_api.agents.tools.subagent.subagent import (
        _DEFAULT_SYSTEM_PROMPT,
        _OUTPUT_FIELDS,
    )
    from vibecanvas_api.agents.tools.subagent.toolset import (
        build_agent_subagent_tools,
    )
    from vibecanvas_api.context import clear_stores, init_stores
    from vibecanvas_api.services.agent_runtime.filesystem_vfs import (
        FilesystemRuntimeVfsStore,
    )

    tenant_id = str(request.tenant_id)
    os.environ["VIBECANVAS_AGENT_RUNTIME_IN_SANDBOX"] = "1"
    state_client = RuntimeStateRpcClient(channel)
    checkpointer = BrokerCheckpointSaver(state_client)

    stop_event = asyncio.Event()
    control_router = RuntimeControlRouter()
    control_task = asyncio.create_task(
        _control_loop(channel, stop_event, None, control_router, state_client)
    )
    vfs_store = FilesystemRuntimeVfsStore()
    init_stores(checkpointer, vfs_store)
    try:
        await channel.send({
            "type": MSG_BACKGROUND_JOB_EVENT,
            "event": {
                "type": "started",
                "progress": {
                    "current": 0,
                    "total": None,
                    "message": "Background subagent started",
                },
            },
        })
        model = _build_chat_model(dict(request.model or {}))
        worker_ctx = AgentContext(
            workflow={},
            vfs=vfs_store,
            username=request.user_id,
            wf_id=request.chat_id,
            chat_id=request.chat_id,
            thread_id=f"sub:{request.chat_id}:{request.job_id}",
            turn_id=request.parent_turn_id,
            tenant_id=tenant_id,
            agent_cfg=dict(request.model or {}),
            runtime_location="sandbox",
            stop_event=stop_event,
        )
        async def publish_trace(entry: dict) -> None:
            if str(entry.get("role") or "").lower() in {
                "system", "human", "user",
            }:
                return
            await channel.send({
                "type": MSG_BACKGROUND_JOB_EVENT,
                "event": {
                    "type": "trace",
                    "trace_entry": entry,
                },
            })

        async def request_nested_tool_approval(
            tool_name: str,
            tool_call_id: str,
            arguments: dict,
        ) -> str:
            if (
                request.approval_owner != "execution_plan"
                or request.approval_mode == "always_allow"
            ):
                return "approved"
            if not tool_call_id:
                return "denied"
            seed = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"vibecanvas:plan-node-approval:{request.job_id}:{tool_call_id}",
            ).hex
            hitl_request_id = f"hitl_plan_node_{seed[:16]}"
            correlation = {
                "source": "plan-node",
                "runtime_request_id": tool_call_id,
                "runtime_item_id": tool_call_id,
                "job_id": request.job_id,
            }
            waiter = asyncio.create_task(
                control_router.wait("plan-node", tool_call_id)
            )
            await channel.send({
                "type": MSG_BACKGROUND_JOB_EVENT,
                "event": {
                    "type": "approval_requested",
                    "approval": {
                        "hitl_request_id": hitl_request_id,
                        "title": f"Approve {tool_name}",
                        "prompt_text": f"Allow this Plan subagent to execute {tool_name}?",
                        "tool_name": tool_name,
                        "arguments": arguments,
                        "runtime_correlation": correlation,
                    },
                },
            })
            response = await waiter
            action = str(response.get("action") or "deny")
            return "approved" if action == "approve" else "denied"

        worker_task = asyncio.create_task(
            run_bounded_agent(
                model=model,
                tools=build_agent_subagent_tools(),
                system_prompt=request.system_prompt or _DEFAULT_SYSTEM_PROMPT,
                user_input=(
                    "# Delegated task\n"
                    f"Title: {request.title}\n\n"
                    "## Complete task packet\n"
                    f"{request.prompt}"
                ),
                output_fields=request.output_fields or _OUTPUT_FIELDS,
                max_iterations=request.max_iterations,
                context=worker_ctx,
                checkpointer=checkpointer,
                thread_id=f"sub:{request.chat_id}:{request.job_id}",
                on_trace=publish_trace,
                request_tool_approval=request_nested_tool_approval,
            )
        )
        stop_task = asyncio.create_task(stop_event.wait())
        done, _ = await asyncio.wait(
            {worker_task, stop_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if stop_task in done:
            worker_task.cancel()
            await asyncio.gather(worker_task, return_exceptions=True)
            result_payload = {
                "status": "cancelled",
                "result": {},
                "error": {"code": "cancelled", "message": "Background job cancelled."},
                "thread_id": f"sub:{request.chat_id}:{request.job_id}",
            }
        else:
            result = worker_task.result()
            result_payload = {
                "status": result.status,
                "result": dict(result.output or {}),
                "trace": [
                    entry for entry in list(result.trace or [])
                    if str(entry.get("role") or "").lower()
                    not in {"system", "human", "user"}
                ],
                "error": (
                    {"code": "subagent_failed", "message": result.error}
                    if result.error else {}
                ),
                "thread_id": f"sub:{request.chat_id}:{request.job_id}",
            }
        stop_task.cancel()
        await asyncio.gather(stop_task, return_exceptions=True)
        await channel.send({
            "type": MSG_BACKGROUND_JOB_RESULT,
            "result": result_payload,
        })
    finally:
        clear_stores(expected_checkpointer=checkpointer)
        state_client.fail_all("background Runtime completed")
        control_task.cancel()
        await asyncio.gather(control_task, return_exceptions=True)


async def _run(channel, request: RuntimeTurnRequest) -> None:
    if request.runtime_type == RuntimeType.CODEX:
        # Import only after the sandbox Python paths have been initialized. The
        # Codex adapter owns its app-server process and never initializes the
        # LangGraph/checkpointer stack above.
        from vibecanvas_api.services.agent_runtime.codex import (
            create_codex_app_server,
            run_codex_turn,
        )

        global _codex_client, _codex_gateways, _codex_threads
        if _codex_client is None:
            _codex_client = create_codex_app_server(request)
        await run_codex_turn(
            channel,
            request,
            client=_codex_client,
            # The app-server is part of the Chat-scoped Runtime and is reused
            # across Turns. Platform capabilities remain Turn-scoped: Codex only
            # receives an ephemeral loopback gateway URL, while the private
            # Authorization header stays inside the gateway and is discarded at
            # the end of the Turn. Every thread/start or thread/resume supplies
            # the current gateway config.
            close_client=False,
            gateway_registry=_codex_gateways,
            resident_threads=_codex_threads,
        )
        return
    await _run_langchain(channel, request)


async def main() -> int:
    socket_path = os.environ.get("VC_BUS_SOCK", "")
    if not socket_path:
        raise RuntimeError("VC_BUS_SOCK is required")
    runtime_type = os.environ.get("VC_AGENT_RUNTIME_TYPE", "")
    if runtime_type:
        _preload_runtime(runtime_type)
    ready_path = os.environ.get("VC_AGENT_RUNTIME_BOOTSTRAP_READY", "")
    if ready_path:
        descriptor = os.open(
            ready_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        os.close(descriptor)
        # A baseline deliberately has no broker. After restore, the same fixed
        # guest path is backed by the target Chat's private host directory.
        await _wait_for_bus_socket(socket_path)

    # In the production proxy profile the Runtime has no direct network device.
    # Start its loopback proxy only after the clean checkpoint boundary so no
    # listener, proxy socket, or connection state is captured in the baseline.
    from vibecanvas_engine.egress_proxy import maybe_start_egress_proxy

    egress_proxy = maybe_start_egress_proxy()
    channel = await connect_bus(socket_path)
    try:
        while True:
            envelope = await channel.recv()
            if envelope is None:
                return 0
            if envelope.get("type") == MSG_BACKGROUND_JOB_REQUEST:
                # Background workers remain one-shot and are owned by their
                # independent job lifecycle.
                request = RuntimeBackgroundJobRequest.model_validate(
                    envelope.get("request") or {}
                )
                await _run_background_subagent(channel, request)
                return 0
            if envelope.get("type") != MSG_RUNTIME_REQUEST:
                raise ValueError(
                    "expected runtime_request or background_job_request"
                )
            try:
                request = RuntimeTurnRequest.model_validate(
                    envelope.get("request") or {}
                )
                await _run(channel, request)
            except Exception as exc:
                await channel.send(
                    {
                        "type": MSG_RUNTIME_ERROR,
                        "error": {
                            "code": "runtime_adapter_failed",
                            "message": str(exc),
                        },
                    }
                )
    except Exception as exc:
        try:
            await channel.send(
                {
                    "type": MSG_RUNTIME_ERROR,
                    "error": {
                        "code": "runtime_adapter_failed",
                        "message": str(exc),
                    },
                }
            )
        except Exception:
            pass
        return 1
    finally:
        # Keep the daemon-thread owner alive for the whole Runtime process.
        _ = egress_proxy
        await _close_codex_runtime_resources()
        await channel.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
