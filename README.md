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
  Build workflows with an agent, refine them on a visual canvas, run them in
  isolated sandboxes, and publish them as APIs, webhooks, schedules, or batch jobs.
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
  <a href="#overview">Overview</a> ·
  <a href="#quick-start">Quick Start</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#architecture">Architecture</a> ·
  <a href="#documentation">Documentation</a> ·
  <a href="SECURITY.md">Security</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

> [!IMPORTANT]
> Skeinix is alpha software. Core workflow, agent, storage, authorization,
> deployment, and sandbox services are implemented, but APIs and data models may
> change before the first stable release. Browser automation is experimental.

## Overview

Skeinix is an open-source, agent-native workflow platform. Describe a goal to an
agent, let it build and refine the real workflow graph, inspect every step on a
visual canvas, and publish the result without moving between separate tools.

What makes Skeinix different:

- 🪄 **Agent-native authoring** — Agents edit and validate the real workflow
  graph instead of returning diagram-shaped suggestions.
- 🎨 **Inspectable execution** — Plans, nodes, outputs, versions, and failures
  stay visible and traceable from Chat to canvas.
- 🧵 **Reusable workflow assets** — Turn dynamic Agent work into durable,
  versioned workflows that can be refined and run again.
- 🚀 **Build to deployment** — Move from an idea to isolated execution, batch
  processing, schedules, APIs, and Webhooks in one platform.
- 🧩 **Composable capabilities** — Combine LangChain or Codex with Knowledge,
  MCP servers, Skills, SubAgents, and experimental browser control.
- 🛡️ **Self-hosted and isolated** — Keep control of models and data with
  sandboxed execution, tenant-aware authorization, audit, and encryption.

## Quick Start

### Installation

#### Docker Compose (recommended)

Docker Engine or Docker Desktop with Compose v2 is required.

```bash
git clone https://github.com/LYuhang/Skeinix.git
cd Skeinix
./scripts/deploy/local_server.sh up
```

The launcher generates local secrets, builds the stack, waits for health checks,
and verifies the deployment. The first build can take several minutes.

#### Native Linux or WSL

For a fresh Debian, Ubuntu, or WSL environment:

```bash
./scripts/bootstrap_native_linux.sh
```

For prerequisites, custom setup, configuration, remote access, and production
guidance, see the [installation guide](docs/installation.md).

### Common Commands

| Action | Docker Compose | Native Linux/WSL |
| --- | --- | --- |
| Start | `./scripts/deploy/local_server.sh up` | `./launch.sh start` |
| Stop | `./scripts/deploy/local_server.sh stop` | `./launch.sh stop` |
| Restart | `./scripts/deploy/local_server.sh restart` | `./launch.sh restart` |
| Status | `./scripts/deploy/local_server.sh status` | `./launch.sh status` |
| Logs | `./scripts/deploy/local_server.sh logs` | `./launch.sh logs` |

Open <http://localhost:9001> after startup verification succeeds. Before your
first Chat, connect a supported model provider or Codex account from Settings.

## Usage

### Main Application

- **Chat** — Work with a LangChain or Codex Agent, attach files, enable MCP
  servers and Skills, and activate platform capabilities with Slash Commands.
- **Workflow** — Create, edit, validate, version, execute, and batch-run visual
  workflows.
- **Task** — Inspect background jobs and manage scheduled runs, cancellation,
  resume, events, and results.
- **Deployment** — Publish workflows as APIs, asynchronous Runs, Webhooks, or
  schedules, then inspect invocation history and metrics.
- **Knowledge** — Upload, index, browse, and retrieve authorized knowledge
  sources.
- **MCP, Skills, and Storage** — Extend Agent capabilities and manage durable
  files and generated artifacts.

#### Chat Slash Commands

Commands activate additional capabilities and remain active in the current
Chat. Multiple commands can be combined when a task crosses capability
boundaries.

| Command | Purpose | Availability |
| --- | --- | --- |
| `/build` | Create, edit, validate, version, and run workflows | Main app and extension; LangChain/Codex |
| `/task` | Inspect and manage Task Center work and scheduled runs | Main app and extension; LangChain/Codex |
| `/deployment` | Inspect and manage workflow deployments | Main app and extension; LangChain/Codex |
| `/knowledge` | Discover and search authorized knowledge bases | Main app and extension; LangChain/Codex |
| `/diagram` | Create, validate, review, and export semantic diagrams | Main app and extension; LangChain/Codex |
| `/plan` | Create a durable dynamic execution plan for SubAgents | LangChain only |
| `/browser` | Control the connected browser and its authenticated pages | Extension side panel only; LangChain/Codex |

### Browser Extension

The experimental Chrome MV3 extension connects the Agent to the user's current
browser session. Build and load it locally:

```bash
cd extension
corepack enable
pnpm install --frozen-lockfile
pnpm test
pnpm build
```

Load `extension/dist` as an unpacked Chrome extension, open its side panel, and
use `/browser` there. Teaching, freezing, self-healing, and batch browser
automation remain under active development.

## Architecture

### Repository Structure

```text
api/        FastAPI control plane, Agent Runtime, auth, storage, and workers
engine/     Framework-independent Python workflow execution engine
web/        React application and visual workflow canvas
extension/  Experimental Chrome MV3 browser integration
docs/       Public installation, architecture, and protocol documentation
scripts/    Bootstrap, deployment, diagnostics, and security utilities
```

### System Architecture

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
            └── sandboxd ─── per-Chat Agent Runtime and workflow sandboxes
```

PostgreSQL is the system of record, OpenFGA and row-level security enforce
authorization boundaries, and untrusted Agent and workflow execution is routed
through the sandbox service. See [Architecture](docs/architecture.md) for the
Runtime lifecycle, MCP boundaries, storage model, and network isolation.

## Documentation

For more detail, see [Installation](docs/installation.md),
[Architecture](docs/architecture.md), and [Development](docs/development.md).

## Security

Do not report vulnerabilities through public GitHub issues. Follow the private
disclosure process in [SECURITY.md](SECURITY.md). Security controls reduce risk
but do not make an alpha deployment automatically suitable for sensitive data.

## Contributing

Contributions are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening
an issue or pull request.

## License

Skeinix is licensed under the [Apache License 2.0](LICENSE). Dependencies retain
their respective licenses; see [Third-party notices](THIRD_PARTY_NOTICES.md).
