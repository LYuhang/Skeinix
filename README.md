<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="web/public/branding/icon-dark.png">
    <source media="(prefers-color-scheme: light)" srcset="web/public/branding/icon-light.png">
    <img src="web/public/branding/icon-light.png" alt="Skeinix" width="168" height="168">
  </picture>
</p>

<h1 align="center">Skeinix</h1>

<p align="center">
  <strong>Build, preview, automate, and deploy with AI agents, visual workflows, tasks, and your browser—all in one platform.</strong>
</p>

<p align="center">
  A self-hosted platform for building, running, and publishing AI-assisted
  automation.
</p>

<p align="center">
  <a href="https://github.com/LYuhang/Skeinix/actions/workflows/ci.yml"><img src="https://github.com/LYuhang/Skeinix/actions/workflows/ci.yml/badge.svg" alt="Continuous integration"></a>
  <a href="https://github.com/LYuhang/Skeinix/actions/workflows/security.yml"><img src="https://github.com/LYuhang/Skeinix/actions/workflows/security.yml/badge.svg" alt="Security gates"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache--2.0-blue.svg" alt="Apache License 2.0"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange.svg" alt="Project status: alpha">
  <img src="https://img.shields.io/badge/Python-3.11.15-3776AB.svg?logo=python&logoColor=white" alt="Python 3.11.15">
  <img src="https://img.shields.io/badge/Node.js-22.23.2-5FA04E.svg?logo=nodedotjs&logoColor=white" alt="Node.js 22.23.2">
</p>

<p align="center">
  English · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
  <a href="#overview">Overview</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

> [!IMPORTANT]
> Skeinix is in alpha. Its core agent, workflow, storage, authorization,
> deployment, and sandbox services are implemented, but APIs and data models may
> change before the first stable release. Browser automation remains
> experimental.

## Overview

Skeinix is an open-source platform for turning an agent conversation into a
runnable workflow. Start with a goal in Chat. The agent builds or modifies the
same workflow graph that appears on the visual canvas, so the structure,
versions, runs, outputs, and failures remain visible throughout the process.

Once the workflow behaves as intended, run it on demand, in a batch, or on a
schedule, or publish it as an API or webhook. Agent and workflow execution is
isolated from the control plane through the sandbox service.

### How Skeinix works

```text
Describe a goal
      ↓
Build or modify a workflow in Chat
      ↓
Inspect and refine the graph on the canvas
      ↓
Run and verify it in an isolated sandbox
      ↓
Reuse it or publish it as an automated service
```

What makes Skeinix different:

- 🪄 **Agent-built workflows** — The agent edits and validates the real workflow
  graph rather than returning a separate suggestion or diagram.
- 🔎 **Visible from intent to result** — Plans, nodes, versions, runs, outputs,
  and failures remain inspectable from Chat and the canvas.
- ♻️ **Reusable automation** — Convert one-off agent work into a durable,
  versioned workflow that can run again or be published.
- 🧩 **Extensible agent capabilities** — Combine LangChain or Codex with MCP
  servers, Skills, Knowledge, SubAgents, and browser control.
- 🛡️ **Self-hosted execution boundaries** — Keep control of models and data
  while routing untrusted execution through isolated sandboxes.

## Quick Start

### Installation

#### Docker Compose (recommended)

Install Docker Engine or Docker Desktop with Compose v2, then start the local
stack:

```bash
git clone https://github.com/LYuhang/Skeinix.git
cd Skeinix
./scripts/deploy/local_server.sh up
```

The launcher generates local secrets, builds the services, waits for their
health checks, and verifies the deployment. The first build can take several
minutes.

#### Native Linux or WSL

Use the native bootstrap when developing on Debian, Ubuntu, or WSL:

```bash
./scripts/bootstrap_native_linux.sh
```

For prerequisites, manual setup, configuration, remote access, and production
guidance, see the [installation guide](docs/installation.md).

### Common Commands

| Action | Docker Compose | Native Linux/WSL |
| --- | --- | --- |
| Start | `./scripts/deploy/local_server.sh up` | `./launch.sh start` |
| Stop | `./scripts/deploy/local_server.sh stop` | `./launch.sh stop` |
| Restart | `./scripts/deploy/local_server.sh restart` | `./launch.sh restart` |
| Status | `./scripts/deploy/local_server.sh status` | `./launch.sh status` |
| Logs | `./scripts/deploy/local_server.sh logs` | `./launch.sh logs` |

Open <http://localhost:9001> after startup verification succeeds.

### Build your first workflow

1. **Choose an Agent runtime.** Open **Settings → Agent runtime** and select the
   default runtime for new Chats:
   - **LangChain** uses model-provider credentials and supports the full
     LangChain toolset, including `/plan`.
   - **Codex** runs conversations through the Codex runtime.

   Only runtimes enabled by the deployment are shown. The selection applies to
   new Chats; an existing Chat keeps the runtime with which it was created.

2. **Connect a model or account.** Complete the connection required by the
   selected runtime:
   - For **LangChain**, open **API Key** from the sidebar, add a credential, and
     enter its provider, model name, and API key. OpenAI, Azure OpenAI,
     Anthropic, Google Gemini, and custom providers are supported.
   - For **Codex**, remain in **Settings → Agent runtime → Codex connection**.
     Sign in with an OpenAI account using the device code, or select **OpenAI
     API** and configure an available company-managed or personal
     OpenAI-compatible API connection.

   The available connection methods depend on the deployment configuration.
   Stored API keys are encrypted and write-only: they cannot be read back from
   the application after saving.

3. **Start a Chat and build the Workflow.** Open **Chat**, create a new
   conversation, and select the connected model if more than one is available.
   Activate `/build`, then describe the automation you want to create, including
   its expected inputs, outputs, and important constraints. Inspect the
   generated Workflow on the canvas, validate and run it, then review the node
   outputs and refine the Workflow in Chat or on the canvas.

The workflow remains available as a versioned asset after the conversation ends.
Publish it only after its inputs, outputs, and failure behavior have been
verified.

## Usage

Most work begins in Chat, where the user describes what they need. When the task
involves an authenticated website, the conversation can start from the browser
extension instead. The agent uses tools to build a Workflow, which the user can
inspect and refine on the canvas. The Workflow can then run directly, through a
batch or scheduled Task, or as a Deployment that external systems can call.

```mermaid
flowchart TB
    G["💡 Describe a goal"]
    B["🌐 Start from the browser extension<br/>for signed-in websites"]
    C["💬 Collaborate with the Agent in Chat"]
    A["🤖 Agent uses tools<br/>to build an executable solution"]
    W["🧩 Inspect and refine<br/>the Workflow on the canvas"]
    R{"Choose how to run"}
    N["▶️ Run now"]
    T["⏱️ Run in a batch or on a schedule"]
    D["🚀 Publish as an API or webhook"]
    O["📦 Review outputs and failures"]
    E["🔌 Connect external systems"]

    G --> C
    B --> C
    C --> A --> W --> R
    R -->|Run| N --> O
    R -->|Task| T --> O
    R -->|Deployment| D --> E
    O -.->|Refine| C
```

The diagram shows a common path, not a set of mandatory dependencies. A Workflow
can be tested directly on the canvas or handed to a Task for batch or scheduled
execution. Once published, external calls and schedules can start new Runs and
Tasks without repeating the original build conversation.

### Main Application

| Surface | What you can do | Demo |
| --- | --- | --- |
| **Chat** | Describe a request in conversation and let a LangChain or Codex agent use tools, build Workflows, create diagrams, and organize files. Preview Workflows, execution plans, background jobs, common documents, tables, media, and diagrams beside the conversation. Each Chat has its own workspace, with a sandbox that starts, hibernates, and restores as needed. | <video src="https://github.com/user-attachments/assets/d71f4e21-f71b-445d-98f4-a20275029405" controls></video> |
| **Workflow** | Add, connect, and configure nodes on a visual canvas, validate the graph, and execute either the full Workflow or an individual node. Review run output, generated files, and earlier versions, or use batch execution and JSON import and export. | <video src="https://github.com/user-attachments/assets/10d47621-7c3b-4bfa-b751-564ef62b507c" controls></video> |
| **Task** | Run a Workflow across a tabular input file or schedule it for a particular time or interval. Task Center shows queue and execution progress, events, output, and failures, and lets users pause, cancel, or resume work where supported. | <video src="https://github.com/user-attachments/assets/095142b4-b42c-4799-af89-0318fca11b10" controls></video> |
| **Deployment** | Publish a verified Workflow as an API, webhook, or scheduled service. Copy endpoints and code examples, test inputs in the UI, review run logs and latency metrics, and manage status, rate limits, and access credentials. | <video src="https://github.com/user-attachments/assets/59dca3ab-7b55-46bc-b73b-232514ab80f5" controls></video> |
| **Knowledge** | Create a knowledge base and upload PDF, Office, text, web, JSON, or tabular sources. The page reports indexing status; once indexed, the agent can find and read relevant material through `/knowledge`. | <video src="https://github.com/user-attachments/assets/7392f893-ecce-4632-a5af-a45bee0b15e1" controls></video> |
| **MCP Server** | Find external tools through the Official MCP Registry or Smithery, or connect a custom server by URL or command. Review the source, requested access, and credential requirements before installation; after connection, the agent loads its tools when needed. | <video src="https://github.com/user-attachments/assets/0fd6c1e5-4349-435f-8b5c-d53abb900c85" controls></video> |
| **Skills** | Find and install reusable instruction packages from sources such as OpenAI and Anthropic, or import a custom Skill. Review its instructions, bundled files, tool requirements, and source before making it available to agents. | <video src="https://github.com/user-attachments/assets/9d6885a1-9a7a-464c-95da-d7921e6cedc9" controls></video> |
| **Storage** | Browse platform files by shared mount, Workflow, Chat, or Task. Search, sort, upload, and download files, and—where permissions allow—create folders, rename or delete items, and preview or edit supported content. | <video src="https://github.com/user-attachments/assets/84876473-f2ab-463a-b48c-f686bf27cee4" controls></video> |

#### Chat Slash Commands

Slash Commands tell Skeinix which specialized capabilities the current
conversation needs. Activating a command gives the agent the corresponding
tools and operating guidance for the rest of that Chat. Commands can be
combined when a task spans more than one area.

| Command | Purpose | Availability |
| --- | --- | --- |
| `/build` | Ask the agent to create or open a Workflow, then modify nodes, validate the graph, create versions, or run it from the conversation | Main app and extension; LangChain/Codex |
| `/task` | Ask the agent to find Tasks, create or update scheduled runs, and cancel or resume work | Main app and extension; LangChain/Codex |
| `/deployment` | Ask the agent to find, create, update, or remove Workflow Deployments | Main app and extension; LangChain/Codex |
| `/knowledge` | Let the agent find and progressively read material from knowledge bases the user can access | Main app and extension; LangChain/Codex |
| `/diagram` | Ask the agent to create a semantic diagram, then validate, render, visually review, and export it | Main app and extension; LangChain/Codex |
| `/plan` | Have the agent organize complex work into a durable execution plan and coordinate SubAgents across its steps | LangChain only |
| `/browser` | Let the agent read or operate tabs and authenticated pages in the connected browser | Extension side panel only; LangChain/Codex |

### Browser Extension

The experimental Chrome MV3 extension connects a Chat to the current browser
session. It is intended for work that must reuse the user's current signed-in
state. Within its authorized scope, the agent can read pages, switch tabs,
click, type, select options, and take screenshots.

Download the extension package that matches the current deployment from
**Settings → Extensions → Download extension**. Extract the ZIP to a permanent
folder, open `chrome://extensions`, enable **Developer mode**, and choose
**Load unpacked**. Select the extracted folder, then pin Skeinix and open its
side panel.

Developers can also build the extension from source:

```bash
cd extension
corepack enable
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

Load `extension/dist` with **Load unpacked**, open the side panel, and activate
`/browser`. The command is unavailable in the main application because browser
control requires the extension's scoped connection to the active tab.

## Architecture

### System at a glance

Skeinix separates platform management from task execution. The Web application
provides Chat, the visual canvas, and management pages. The FastAPI control
plane handles identity, authorization, persistence, orchestration, and live
event delivery. `sandboxd` places Agent and Workflow execution in isolated
sandboxes, while workers process background jobs, scheduled runs, knowledge
indexing, and batch execution.

```text
Browser / Chrome extension
            │
            ▼
        Web / Nginx
            │
            ▼
      FastAPI control plane ─── PostgreSQL / OpenFGA / object storage
            │
            ├── Valkey / Celery workers
            └── sandboxd ─── per-Chat agent runtime and workflow sandboxes
```

PostgreSQL is the system of record. OpenFGA and row-level security enforce
authorization boundaries, object storage holds durable file content, and
Valkey provides queueing and transient coordination. The sandbox service keeps
agent and workflow execution outside the API process.

See the [architecture guide](docs/architecture.md) for runtime lifecycle, MCP
boundaries, storage ownership, authorization, and network isolation.

### Repository structure

```text
api/        FastAPI control plane, agent runtime, auth, storage, and workers
engine/     Framework-independent Python workflow execution engine
web/        React application and visual workflow canvas
extension/  Experimental Chrome MV3 browser integration
docs/       Public installation, architecture, security, and development guides
scripts/    Bootstrap, deployment, diagnostics, and security utilities
```

## Documentation

| Goal | Document |
| --- | --- |
| Install, configure, or troubleshoot a self-hosted instance | [Installation and deployment](docs/installation.md) |
| Understand components, runtime flow, storage, and isolation | [Architecture](docs/architecture.md) |
| Prepare and operate a production deployment | [Production deployment](DEPLOY.md) |
| Understand security controls and data lifecycle | [Security and data lifecycle](docs/security-and-data-lifecycle.md) |
| Set up a development environment and run checks | [Development guide](docs/development.md) |
| Contribute code or documentation | [Contributing guide](CONTRIBUTING.md) |

## Security

Do not report vulnerabilities through public GitHub issues. Follow the private
disclosure process in [SECURITY.md](SECURITY.md). Review the documented trust
boundaries and deployment requirements before using alpha releases with
sensitive data.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening
an issue or pull request.

## License

Skeinix is licensed under the [Apache License 2.0](LICENSE). Dependencies retain
their respective licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).


<!-- Product demo videos use GitHub user attachments for inline playback. -->
