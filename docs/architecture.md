# Architecture

Skeinix is a self-hosted platform for turning agent-assisted work into
inspectable, reusable automation. Its architecture separates two concerns: the
control plane handles authentication, authorization, orchestration, and
persistent data; the execution plane runs agent and workflow code in isolated
environments.

This document describes the current architecture as implemented in the
repository. Installation and configuration belong in the
[installation guide](installation.md); production requirements belong in the
[deployment guide](../DEPLOY.md).

## Architecture overview

The architecture follows four principles:

1. **A shared workflow representation.** Agents and users edit the same
   versioned graph shown on the canvas. There is no separate agent-only workflow
   format.
2. **Separation of orchestration and execution.** The API coordinates work and
   records its state, while `sandboxd` runs agent and workflow code outside the
   API process.
3. **Clear storage responsibilities.** PostgreSQL stores application records,
   object storage holds file content, and Valkey supports queues and temporary
   coordination.
4. **Server-side authorization.** The backend checks access before database,
   storage, model, or browser operations. A resource identifier supplied by a
   browser or sandbox is never treated as proof of access.

```text
Browser UI ── HTTP / SSE ──┐
                           ▼
Chrome extension ── WS ── Web / nginx ──► FastAPI control plane
                                               │
                 ┌─────────────────────────────┼──────────────────────┐
                 ▼                             ▼                      ▼
       PostgreSQL / OpenFGA /           Valkey / Celery            sandboxd
          object storage                    workers                   │
                                                                       ▼
                                                         gVisor agent runtimes
                                                         and workflow execution
```

The React application provides Chat, canvas, Task, Deployment, Storage, and
administration pages. FastAPI authenticates requests, checks access, stores
application data, coordinates background jobs and sandboxes, and streams
updates to clients. The default Compose deployment and its service connections
are defined in [`docker-compose.yml`](../docker-compose.yml).

## Core domain model

| Concept | Role | Current implementation |
| --- | --- | --- |
| **Organization** | Tenant boundary used to isolate users, permissions, and application resources | [Organization models](../api/src/vibecanvas_api/storage/models_org.py) |
| **Chat** | Persistent agent workspace containing messages, commands, runtime settings, and optional browser-control state | [Chat models](../api/src/vibecanvas_api/storage/models.py) |
| **Agent Run** | Persisted record of one agent response, including ordered events, approval waits, cancellation, and final status | [Agent Run models](../api/src/vibecanvas_api/storage/models_agent_runs.py) |
| **Execution Plan** | Task graph created through `/plan` to coordinate Agent and SubAgent work within a Chat | [Execution Plan models](../api/src/vibecanvas_api/storage/models_execution_plans.py) |
| **Workflow** | Reusable automation graph composed of validated nodes and references | [Workflow model](../api/src/vibecanvas_api/storage/models.py) |
| **Workflow Version** | Stored major/subversion snapshot used for history and version selection | [Workflow repository](../api/src/vibecanvas_api/storage/workflow_repo.py) |
| **Workflow Run** | One execution of a workflow or node, with status and events that the UI can reload | [Execution API](../api/src/vibecanvas_api/routes/executions.py) |
| **Task** | Persistent record for batch or scheduled work, including progress, results, and cancellation | [Task models](../api/src/vibecanvas_api/storage/models_tasks.py) |
| **Deployment** | API, webhook, or cron interface bound to the workflow head or a specific version | [Deployment model](../api/src/vibecanvas_api/storage/models_deployments.py) |
| **VFS** | Virtual file system that presents logical paths while storing file content in the configured object store | [VFS store](../api/src/vibecanvas_api/storage/vfs_store.py) |

An Execution Plan and a Workflow solve different problems. An Execution Plan
organizes the work required to complete a complex Chat request. A Workflow
defines automation that can be versioned, run repeatedly, and published. They
are stored and executed independently.

## Components and responsibilities

### Web application

The React and Vite application provides the user interface and manages
browser-local interaction. Its main areas include Agent Chat, the Workflow
canvas, Tasks, Deployments, Knowledge, MCP servers, Skills, Storage, and
Settings. Routing and page composition start in the
[application router](../web/src/app/router.tsx), while the visual editor is
implemented under the [canvas pages](../web/src/pages/canvas/).

The Web application communicates with the API over HTTP and server-sent events.
Authorization is enforced by the API, and workflow code is never executed in
the browser.

### FastAPI control plane

The FastAPI service is the central entry point for application operations. It
is responsible for:

- authentication, sessions, and active organization selection;
- OpenFGA permission checks and PostgreSQL tenant isolation;
- Chat, Workflow, Task, Deployment, Knowledge, and VFS APIs;
- Agent Run coordination, approval state, and client event streams;
- secure handling of credentials and temporary access tokens;
- communication with Celery workers and `sandboxd`.

The application factory shows the complete router and service composition in
[`app.py`](../api/src/vibecanvas_api/app.py). HTTP and streaming contracts are
grouped under [`routes/`](../api/src/vibecanvas_api/routes/).

### Agent Runtime

A Chat selects either the LangChain or Codex runtime when it first starts. The
API sends both runtimes the same internal request format, and each adapter
translates that request into the format expected by its SDK. SDK-specific state
and event formats are therefore not exposed to the rest of the application.
The common request contains the message, attachments, selected model, active
commands, Model Context Protocol (MCP) connections, Skills, todo state, and
references to interactive artifacts.

This common interface is defined in the
[runtime protocol](../api/src/vibecanvas_api/services/agent_runtime/protocol.py).
Runtime selection and conversion into the application's common event format
are centralized in the
[runtime orchestrator](../api/src/vibecanvas_api/services/agent_runtime/orchestrator.py);
the concrete adapters live in
[`langchain.py`](../api/src/vibecanvas_api/services/agent_runtime/langchain.py)
and [`codex_runtime.py`](../api/src/vibecanvas_api/services/agent_runtime/codex_runtime.py).

### Workflow engine

`vibecanvas-engine` validates and executes workflow graphs independently of
FastAPI, the Web application, and the application database. It checks graph
structure, node relationships, and input references. Each node class defines
the validation and execution behavior for one supported node type.

The graph implementation is in
[`workflow.py`](../engine/src/vibecanvas_engine/workflow.py), and the node
catalog is under [`nodes/`](../engine/src/vibecanvas_engine/nodes/). The API
provides the surrounding permission checks, persistence, event streaming, and
sandbox integration.

### Workers

Celery workers perform batch execution, scheduled runs, deployment invocations,
Knowledge indexing, and maintenance work. Valkey carries Celery jobs and
short-lived results. PostgreSQL stores the Task and event history shown after a
page refresh or service restart.

Queue configuration is defined in
[`celery_app.py`](../api/src/vibecanvas_api/celery_app.py), with job entry points
under [`celery_tasks/`](../api/src/vibecanvas_api/celery_tasks/).

### Sandbox service

`sandboxd` manages gVisor sandboxes, active sessions, mounted directories,
snapshots, agent runtime processes, and controlled network access. The API and
workers call it through a private Unix socket instead of starting gVisor
themselves. In the default Compose stack, `sandboxd` is the only application
service that runs with elevated container privileges.

The daemon interface is implemented in
[`service.py`](../api/src/vibecanvas_api/services/sandbox/service.py), session
lifecycle and resource management in
[`manager.py`](../api/src/vibecanvas_api/services/sandbox/manager.py), and the gVisor provider in
[`gvisor.py`](../api/src/vibecanvas_api/services/sandbox/gvisor.py).

### Browser extension

The optional Chrome MV3 extension embeds Chat in a browser side panel and opens
an authenticated WebSocket connection limited to the current browser-control
session. The official Playwright MCP owns browser semantics in the Chat
sandbox. The extension service worker is only its remote CDP data plane: a
fixed five-command relay allow-list attaches approved tabs, forwards CDP
messages, and reports tab lifecycle events. It has no Skeinix-specific DOM
query/action protocol and accepts no arbitrary JavaScript command.

The relay allow-list and dispatch live in
[`relay-executor.ts`](../extension/src/playwright/relay-executor.ts), the
upstream-derived target model in
[`browser-model.ts`](../extension/src/playwright/browser-model.ts), and Chrome
integration in
[`service-worker.ts`](../extension/src/service-worker.ts). Build-time origins
and runtime sender checks share the allowlist in
[`config.ts`](../extension/src/shared/config.ts).

## Main runtime flows

### Chat turn

```text
User message
    │
    ▼
Authorize Chat and resolve the leading Slash Command
    │
    ▼
Create an Agent Run and assemble a common runtime request
    │
    ▼
Start or resume the Chat sandbox and selected Agent Runtime
    │
    ▼
Persist ordered events ──► stream updates to the client over SSE
    │
    ├── record and wait for approval or interaction
    └── complete, cancel, or fail the Agent Run
```

The Chat API authenticates the user, checks access to the Chat, resolves the
Slash Command available in the main application or extension, and loads the
runtime selected for that Chat. It then creates an Agent Run as the system of
record for the turn. The runtime orchestrator calls the appropriate adapter and
converts its output into a unified event schema.

Events are persisted before they are sent through Server-Sent Events (SSE).
The client can therefore reconnect, reload missed events, or resume an approval
flow without relying on the original HTTP connection.

Relevant code paths are the [Chat API](../api/src/vibecanvas_api/routes/chats.py),
[command registry](../api/src/vibecanvas_api/agents/commands/registry.py),
[Agent Run repository](../api/src/vibecanvas_api/storage/agent_runs_repo.py),
and [approval repository](../api/src/vibecanvas_api/storage/hitl_repo.py).

### Agent tools and MCP integration

Slash Commands activate a defined set of tools and instructions for a Chat.
Every turn receives the core Platform MCP servers. Commands such as `/build`
and `/task` add their Platform MCP capability. `/browser` instead starts the
pinned official Playwright MCP in the Chat sandbox and gives it a scoped remote
CDP connection to the extension. `/plan` is available only with the LangChain
runtime, and `/browser` is available only in the extension side panel.

Platform MCP servers expose backend capabilities through the Model Context
Protocol. They remain in the control plane because their tools require
authenticated access to PostgreSQL, OpenFGA, VFS, deployments, or browser
control. Remote custom MCP connections pass through a backend proxy so their
credentials are not copied into the agent sandbox. A local MCP server using
`stdio` can run inside the Chat sandbox when its configuration contains no
stored credentials.

Command-to-MCP routing and custom MCP connection handling are implemented in
[`mcp.py`](../api/src/vibecanvas_api/services/agent_runtime/mcp.py). Platform
servers and tool groups are assembled in
[`server.py`](../api/src/vibecanvas_api/services/platform_mcp/server.py).

### Workflow editing and execution

```text
Canvas or /build
    │
    ▼
Validate graph and node references
    │
    ▼
Save workflow and version state
    │
    ▼
Submit a workflow or node execution
    │
    ▼
Run through sandboxd and vibecanvas-engine
    │
    ▼
Persist status and events; write large data to the run-specific VFS
```

Chat tools and the canvas read and update the same persisted Workflow model.
Before execution, the API validates the graph and applies an admission policy:
only node types supported by the workflow engine are sent to the sandbox.
Operations that require backend access remain in control-plane services.

For interactive runs, PostgreSQL stores only the status and ordered events
needed to restore the canvas after a refresh. Large inputs, outputs, and files
are stored in a VFS namespace dedicated to that run. This keeps control-plane
records small while preserving execution data for later inspection.

This flow is implemented in the [execution routes](../api/src/vibecanvas_api/routes/executions.py),
[sandbox admission policy](../api/src/vibecanvas_api/services/sandbox/workflow_guard.py),
[Workflow Run models](../api/src/vibecanvas_api/storage/models.py), and
[run-scoped VFS repository](../api/src/vibecanvas_api/storage/vfs_run_repo.py).

### Tasks and deployments

A Task is the persistent job record for batch execution (`batch_exec`) or a
scheduled run (`scheduled_run`). Its event log contains status changes,
progress, logs, results, and the final outcome. A Task schedule adds cron or
interval timing, input presets, concurrency policy, and notification settings.

A Deployment exposes a Workflow as one of three trigger types:

- **API**, authenticated with a deployment API key;
- **Webhook**, authenticated with an HMAC secret;
- **cron**, driven by a configured expression and timezone.

A Deployment can track the current Workflow version or pin an explicit major
and subversion. The invocation endpoint authenticates the caller, resolves the
configured version, and submits asynchronous work to Celery. Workflow code is
still executed through the sandbox service rather than inside the worker.

See the [Task API](../api/src/vibecanvas_api/routes/tasks.py),
[Deployment API](../api/src/vibecanvas_api/routes/deployments.py),
[invocation routes](../api/src/vibecanvas_api/routes/deployment_invoke.py), and
[deployment worker](../api/src/vibecanvas_api/celery_tasks/deployment_invoke.py).

### Browser control

`/browser` is available only in a Chat opened from the extension side panel.
The extension establishes an authenticated control channel, and the backend
stores which Chat currently holds that browser session. The selected Agent
Runtime starts the pinned official Playwright MCP inside the Chat sandbox.
Playwright owns page snapshots, locators, actionability, waiting, dialogs, tabs,
screenshots, and tool schemas. Its CDP connection is carried through a
short-lived, Chat- and generation-fenced WebSocket capability to the extension;
the browser never exposes a public debugging port.

The backend path starts in the
[Runtime MCP descriptor](../api/src/vibecanvas_api/services/agent_runtime/mcp.py),
passes through the authenticated
[browser relay route](../api/src/vibecanvas_api/routes/browser.py), and reaches
the selected extension through the
[transport registry](../api/src/vibecanvas_api/browser/registry.py). The
reviewed Agent-facing tool allow-list is centralized in
[`playwright_contract.py`](../api/src/vibecanvas_api/browser/playwright_contract.py);
unrestricted page evaluation and remote-code tools are rejected both when tools
are listed and when a call is forwarded.

## Data and state management

| Component | Primary role | Notes |
| --- | --- | --- |
| **PostgreSQL** | System of record for Organizations, users, Chats, messages, Workflows, versions, runs, approvals, Tasks, Deployments, metadata, and ordered events | Tenant-specific business tables use row-level security |
| **OpenFGA** | Relationship-based access control (ReBAC) | Evaluates whether a user can perform an action on a resource |
| **Object storage** | File content for VFS, artifacts, Knowledge sources, Task outputs, and run files | Filesystem and S3 backends implement the same storage interface |
| **Valkey** | Message broker and transient coordination | Carries Celery jobs, short-lived notifications, and locks; it is not the system of record |
| **Runtime state** | LangChain checkpoints and SDK-specific Chat state | Persists independently of live network connections; it may use the same PostgreSQL cluster |
| **Runtime volumes and snapshots** | Chat-specific runtime files and optional gVisor checkpoints | Used to resume execution efficiently, not to determine identity or permissions |

VFS metadata and file content are separated by the
[VFS store](../api/src/vibecanvas_api/storage/vfs_store.py) and
[object-store providers](../api/src/vibecanvas_api/services/object_store.py).
Runtime checkpoints are accessed through
[`checkpoint_store.py`](../api/src/vibecanvas_api/services/agent_runtime/checkpoint_store.py).
Encryption and retention behavior are described in
[Security and data lifecycle](security-and-data-lifecycle.md).

## Authorization and execution boundaries

Skeinix uses defense in depth for data access. OpenFGA provides
relationship-based access control (ReBAC): it determines whether the current
user may perform an action on a specific resource. PostgreSQL row-level
security (RLS) independently restricts database rows to the active tenant. Both
the authorization context and tenant context are derived from the authenticated
request; a sandbox cannot select its own tenant.

Platform and custom MCP proxies use short-lived credentials limited to the
current organization, user, Chat, Agent Run, and MCP server. Before performing
a protected operation, the backend checks the current database records and
resource permissions again. The sandbox receives only the files and
credentials required for the current operation.

The main implementations are the
[authorization service](../api/src/vibecanvas_api/authorization/openfga.py),
[tenant-bound database sessions](../api/src/vibecanvas_api/storage/db.py), and
[temporary MCP credential
implementation](../api/src/vibecanvas_api/services/platform_mcp/capability.py).

## Sandbox lifecycle

When snapshot mode is enabled, `sandboxd` manages interactive Chat and Workflow
Debug sessions through the following lifecycle:

```text
Released ── acquire ──► Warm ── idle ──► Hibernating ──► Hibernated
                          ▲                                  │
                          └──────────── Restoring ◄──────────┘

Warm / Hibernated ── release ──► Releasing ──► Closed
Hibernating / Restoring ── failure ──► Snapshot failed ──► Releasing
```

`warm`, `hibernating`, `hibernated`, `restoring`, `releasing`,
`snapshot_failed`, and `closed` are lifecycle states. `busy` and `idle` are
separate activity observations while a session is warm. `released` is the
status reported when no session is currently loaded, not an internal session
state.

Before hibernation, `sandboxd` finishes pending file writes, synchronizes the
runtime volume, and stops the Agent Runtime process. Live network connections
and temporary credentials for the current turn are therefore excluded from the
checkpoint. When the session resumes, these connections and credentials are
created again. If snapshot mode is disabled, an idle session is released
directly instead of being hibernated.

Reusable baseline snapshots and Chat-specific hibernation snapshots are stored
separately and cannot be used interchangeably. The lifecycle states and valid
transitions are defined in
[`session_lifecycle.py`](../api/src/vibecanvas_api/services/sandbox/session_lifecycle.py);
transition behavior and idle sweeping are implemented in
[`manager.py`](../api/src/vibecanvas_api/services/sandbox/manager.py).

## Network boundaries

The default Compose stack uses network segmentation: browser-facing traffic,
application data, authorization, sandbox control, and outbound traffic use
separate Docker networks. Web/nginx is attached only to the edge network. The
API reaches PostgreSQL and Valkey through the data network, OpenFGA through the
authorization network, and `sandboxd` through the control network. OpenFGA has
a separate network for its own database.

The default snapshot profile configures gVisor with `SANDBOX_NETWORK=none` and
`SANDBOX_EGRESS_MODE=proxy`. A controlled egress proxy handles outbound HTTP(S)
and WebSocket traffic. It allows public destinations according to policy and
private destinations only when they are explicitly configured. Host-network
mode is a development option for trusted workloads that require protocols the
proxy does not support.

The deployable topology is the source of truth in
[`docker-compose.yml`](../docker-compose.yml). Egress validation and relay
behavior are implemented in
[`egress_policy.py`](../api/src/vibecanvas_api/services/sandbox/egress_policy.py)
and [`egress_broker.py`](../api/src/vibecanvas_api/services/sandbox/egress_broker.py).

## Repository map

| Path | Responsibility |
| --- | --- |
| [`api/src/vibecanvas_api/routes/`](../api/src/vibecanvas_api/routes/) | HTTP, WebSocket, and SSE contracts |
| [`api/src/vibecanvas_api/services/agent_runtime/`](../api/src/vibecanvas_api/services/agent_runtime/) | Common runtime interface, adapters, and orchestration |
| [`api/src/vibecanvas_api/services/platform_mcp/`](../api/src/vibecanvas_api/services/platform_mcp/) | Agent tools that require backend access |
| [`api/src/vibecanvas_api/services/sandbox/`](../api/src/vibecanvas_api/services/sandbox/) | Sandbox service, lifecycle, gVisor, and egress |
| [`api/src/vibecanvas_api/storage/`](../api/src/vibecanvas_api/storage/) | Database models and repositories |
| [`api/src/vibecanvas_api/authorization/`](../api/src/vibecanvas_api/authorization/) | OpenFGA model and enforcement |
| [`api/src/vibecanvas_api/celery_tasks/`](../api/src/vibecanvas_api/celery_tasks/) | Background and scheduled jobs |
| [`engine/src/vibecanvas_engine/`](../engine/src/vibecanvas_engine/) | Workflow graph and node runtime |
| [`web/src/`](../web/src/) | React application and visual Workflow editor |
| [`extension/src/`](../extension/src/) | Chrome MV3 side panel and browser-control bridge |

The main dependency direction is deliberate: the Web application calls the
API; the API coordinates the engine and external services; the engine does not
depend on the API or Web application.

For operational details, continue with
[Installation and deployment](installation.md). For trust assumptions,
retention, and vulnerability reporting, see
[Security and data lifecycle](security-and-data-lifecycle.md) and
[`SECURITY.md`](../SECURITY.md).
