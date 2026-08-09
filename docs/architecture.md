# Architecture

Skeinix is a service-oriented monorepo with a framework-independent execution
engine, an API control plane, asynchronous workers, a browser client, and an
independent sandbox service.

## Components

### Web application

The React and Vite application provides chat, workflow editing, execution
inspection, deployments, knowledge sources, credentials, MCP servers, skills,
storage, tasks, and organization settings. It communicates with the API over
HTTP and server-sent events.

### API and workers

The FastAPI service owns authentication, authorization, workflow persistence,
agent orchestration, deployment APIs, and user-facing streams. Celery workers
execute background, scheduled, indexing, and batch jobs. PostgreSQL is the
system of record and Valkey provides Redis-compatible queues and transient
coordination.

### Workflow engine

`vibecanvas-engine` validates and executes workflow graphs without depending on
FastAPI or the Web application. Nodes expose a consistent validation and result
contract so agents can inspect failures and repair graphs.

### Sandbox service

Untrusted workflow and agent execution is delegated to the sandbox service.
Local production-like environments use gVisor. The API process does not use an
in-process fallback for user code. Files are projected into explicit run and
store roots, and outbound network access can be mediated by an egress broker.
In the Compose topology, only `sandboxd` is rootful and privileged; every other
application service remains unprivileged. Interactive Chat and Workflow Debug
sandboxes move from warm to checkpointed to released state only after the
in-sandbox activity observer reports sustained silence. Batch, scheduled,
webhook, and deployment runs are one-shot.

### Agent Runtime and MCP boundary

Each Chat owns one sandbox Runtime process, workspace, lifecycle, and set of MCP
client sessions. Codex receives only loopback URLs for Platform MCP: a
Turn-local gateway inside the Chat sandbox keeps capability headers and approval
policy outside the native Codex process, then forwards approved calls to the
backend. The baseline `config`, `interactive`, and `workflow` gateways start in
parallel to keep first-Turn latency bounded. Custom stdio MCP processes execute
inside the same Chat sandbox.

Privileged Platform MCP handlers remain stateless backend services because they
need authenticated access to PostgreSQL, OpenFGA, VFS, and audit facilities.
Every capability is scoped to the organization, user, Chat, Turn, Runtime
session, browser session generation, and MCP server. The backend revalidates
durable identity and active-run ownership rather than trusting sandbox state.

The live Agent Runtime and its network sockets are never checkpointed. On
hibernation, sandboxd stops the Runtime and MCP gateways before saving only the
credential-free file/workflow worker. Resume creates fresh gateways and fresh
Turn capabilities, so stale authentication or TCP state cannot cross a snapshot
boundary.

Sandbox lifecycle is Runtime-neutral. Codex Account, Codex API, and LangChain
use the same daemon-owned state machine and TTLs; adapters differ only in where
they persist native state and how authentication is injected. A clean baseline
snapshot is a reusable startup cache, while a session-hibernation snapshot is
owned by exactly one organization and Chat. The two kinds live in separate
snapshot namespaces and cannot be restored interchangeably.

| State | Live mounts and processes | Durable state | Authentication |
| --- | --- | --- | --- |
| Released | None | VFS and Runtime Volume retained | Detached |
| Warm / busy | VFS, Runtime, Bus, and egress attached | Continuously persisted | Current Turn capability or Codex Account bind |
| Warm / idle | Same resident process and mounts | Retained | Turn capabilities removed; Account bind remains for hot reuse |
| Hibernating | New work fenced; writeback and Runtime shutdown in progress | VFS and Runtime state synchronized first | Capabilities removed and Account bind detached before checkpoint |
| Hibernated | No live gVisor process or active mount | VFS projection, Runtime Volume, and Chat snapshot retained | Detached |
| Restoring | Mounts, Runtime, Bus, and egress reconstructed | Snapshot fingerprint and VFS projection verified | Revalidated and freshly injected |

The first idle TTL moves Warm to Hibernated. The second removes the Chat
snapshot and host materialization but does not delete durable VFS, Runtime
Volume data, or the user's account connection. Chat deletion owns durable data
deletion; account disconnect owns authentication revocation.

### Authorization and storage

OpenFGA provides relationship-based authorization. PostgreSQL row-level
security and explicit tenant context provide a second enforcement layer.
Sensitive content uses envelope encryption, while the virtual file system maps
logical paths to encrypted object storage.

### Network boundaries

The local stack uses several narrow Compose networks rather than one shared
bridge. This preserves stable service discovery while preventing a compromised
edge or sandbox process from discovering every datastore.

```text
Browser
   │ published :9001
   ▼
Web/nginx ── edge ── API
                       ├── data ── PostgreSQL / Valkey
                       ├── authorization ── OpenFGA ── authorization_data
                       └── control ── sandboxd
                                          │ private UDS
                                          ▼
                                  per-Chat gVisor Runtime
                                          │
                          HTTP(S)/WebSocket egress broker
                                          │
                                public or approved targets
```

Nginx resolves the API by its Compose service name and refreshes Docker DNS, so
recreating the API container does not leave the Web container pinned to a stale
address. Browser traffic never reaches internal service ports directly.

The sandbox control channel is a private Unix socket. In the production proxy
profile, each active Agent Runtime has `network=none` but receives its own
loopback web proxy backed by a per-Runtime host socket. The relay permits the
exact private Platform MCP/model-broker origin and applies the configured policy
to public destinations. Database, Valkey, OpenFGA, cloud metadata, loopback, and
other private addresses remain unreachable unless an operator grants an exact
private `host:port`. Development can select host-network mode when a tool needs
non-HTTP protocols; that broader reach is an explicit trust decision.

### Browser extension

The optional Chrome MV3 extension maintains a scoped WebSocket connection in an
offscreen document. A service worker executes an allowlisted command vocabulary
through Chrome DevTools Protocol. The extension does not evaluate remote code.

## Main data flow

```text
Browser UI
    |
    v
FastAPI control plane ---- PostgreSQL / OpenFGA / object storage
    |
    +---- Valkey (Redis protocol) ---- Celery workers
    |
    +---- sandbox service ---- workflow engine
    |
    +---- scoped WebSocket ---- Chrome extension (experimental)
```
