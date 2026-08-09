# Installation and deployment

This guide covers the supported self-hosted paths. Use Docker Compose for the
shortest evaluation path and native Linux setup for active development.

## Docker Compose

### Prerequisites

- Docker Engine
- Docker Compose v2
- OpenSSL
- 4 CPU cores and 8 GiB RAM recommended

Start the stack:

```bash
./scripts/deploy/local_server.sh up
```

The launcher drives the repository's single local orchestration file,
`docker-compose.yml`. Component Dockerfiles are build inputs for Compose, not
separate installation entry points.

On first run the script copies `.env.example` to `.env`, generates independent
local secrets, sets file permissions to `0600`, builds every service, waits for
health checks, and runs smoke verification.

To initialize without starting:

```bash
./scripts/deploy/local_server.sh init
```

Edit `.env` only after initialization. At minimum, review:

- `VIBECANVAS_PUBLIC_URL`
- `VIBECANVAS_BIND_ADDRESS`
- model/provider configuration
- object storage settings
- sandbox egress mode

Then start or restart:

```bash
./scripts/deploy/local_server.sh up
./scripts/deploy/local_server.sh restart
```

## Native Debian, Ubuntu, or WSL

Native installation always uses a repository-local uv environment at `.venv`.
The launchers do not activate Conda, install into the host Python, or fall back
to a system interpreter. If `.venv` is missing, run the bootstrap again.

The automated bootstrap runs as a normal user. It invokes `sudo` only while
installing operating-system packages (PostgreSQL, a Redis-compatible service,
build tools, fonts, and supporting libraries); your system may prompt for the
sudo password at that point. Do not run the whole script as root.

```bash
./scripts/bootstrap_native_linux.sh
```

Prepare dependencies without starting services:

```bash
./scripts/bootstrap_native_linux.sh --prepare-only
```

After preparation, manage the stack with:

```bash
./launch.sh start
./launch.sh status
./launch.sh logs
./launch.sh restart
./launch.sh stop
```

Native setup uses a repository-local `.venv` and a private
`.env.launch.local`. Both are ignored by Git.

Python dependencies are installed from the fully pinned
`requirements-dev.txt`. Production images use hash-locked runtime subsets;
their disposable Python build backends are separately hash-locked and removed
from final images. JavaScript packages use committed pnpm lockfiles with
`--frozen-lockfile`.

Tests that contact an external package registry are disabled by default. Set
`SKEINIX_TEST_NETWORK=1` only on a runner whose direct sandbox egress is
intentionally available.

## Manual development setup

Install PostgreSQL, Valkey or another Redis-compatible service, Node.js 22,
pnpm 10.34.4, and uv. The automated Debian/Ubuntu bootstrap uses the
distribution's `redis-server` package for native development; Docker Compose
uses the BSD-licensed Valkey image. Let uv download and manage the project
interpreter; a preinstalled host Python is not required by the application
environment.
Then run:

```bash
uv python install 3.11.15
uv venv --python 3.11.15 --seed .venv
uv pip install --python .venv/bin/python --require-hashes --requirement requirements-build.txt
uv pip install --python .venv/bin/python --requirement requirements-dev.txt
uv pip install --python .venv/bin/python --no-build-isolation --no-deps --editable ./engine --editable ./api
source .venv/bin/activate

corepack enable
corepack prepare pnpm@10.34.4 --activate
sudo npm install --global --ignore-scripts --no-audit --no-fund @openai/codex@0.147.0
test "$(codex --version)" = "codex-cli 0.147.0"
pnpm --dir web install --frozen-lockfile
pnpm --dir extension install --frozen-lockfile
```

Database migrations are managed through Alembic in `api/`.

Do not set `VIBECANVAS_PYTHON` to Conda or a host interpreter. If an explicit
value is needed, it must resolve to this checkout's `.venv/bin/python`.

Launchers resolve the repository root from their own location and do not
persist the checkout's absolute path. Native process state and logs use
`/tmp/vibecanvas-native` by default; set `VIBECANVAS_NATIVE_RUNTIME_DIR` before
invoking `launch.sh` to relocate them.

## Docker environment boundary

Docker installation does not create or use a Python environment on the host.
All Python packages and executables live in the API image and its containers;
the host only needs Docker Engine, Docker Compose, OpenSSL, and the repository.
The API image installs the reviewed Codex CLI `0.147.0` native bundle in a
disposable Node build stage; Node.js and npm are not present in the final image.

Docker Desktop with WSL2 integration can run the Compose stack and provides its
daemon from Docker Desktop's own Linux VM. Do not run
`scripts/install_docker_ubuntu.sh` in that case: installing a second daemon in
the WSL distribution creates ambiguous sockets and separate image/volume stores.
It is normal for `systemctl is-active docker` to report inactive when the active
daemon is Docker Desktop. Rootful snapshot support must still pass the project's
`sandbox_prewarm` checkpoint/restore probe on the actual Desktop kernel.

Starting or configuring the Docker daemon generally requires administrator
access. On a rootful Docker installation, the daemon runs with host-level root
authority, but application containers do not automatically receive all host
capabilities. The Compose stack grants those capabilities only to `sandboxd`;
Web, API, Celery, databases, and migration services remain unprivileged.

Docker defaults `SANDBOX_TYPE` to `rootful-snapshot`. Before API or worker
traffic starts, `sandbox_prewarm` asks sandboxd to cold boot a credential-free
worker, checkpoint it, restore it, and execute a real channel probe. Unsupported
hosts fail this startup gate without falling back to a different sandbox type.
Rootless native development defaults to `rootless-warm`, because gVisor does not
support save/restore in rootless mode. See the gVisor
[rootless](https://gvisor.dev/docs/user_guide/rootless/) and
[checkpoint/restore](https://gvisor.dev/docs/user_guide/checkpoint_restore/)
documentation.

## Configuration reference

Docker reads the repository-local `.env`; `local_server.sh init` creates it with
mode `0600` and generates independent development secrets. Native installation
uses the ignored `.env.launch.local`. Restart the affected services after a
change. The complete machine-readable template remains [.env.example](../.env.example);
the tables below cover the settings operators normally need to understand.

### Listener and browser URLs

| Variable | Local default | Purpose |
| --- | --- | --- |
| `VIBECANVAS_PUBLIC_URL` | `http://localhost:9001` | Canonical browser-visible origin used for links and OAuth callbacks |
| `VIBECANVAS_BIND_ADDRESS` | `127.0.0.1` | Host address used for published Compose ports; keep loopback unless direct LAN access is intentional |
| `VIBECANVAS_HTTP_PORT` | `9001` | Web UI port |
| `VIBECANVAS_API_PORT` | `8000` | Direct API port used for diagnostics and development |
| `WEB_ALLOWED_HOSTS` | derived | Additional exact hosts accepted by the web server |
| `VIBECANVAS_API_CORS_ORIGINS` | derived | Additional exact browser origins allowed to call the API |
| `TRUSTED_PROXY_CIDRS` | private networks | Reverse-proxy networks allowed to provide forwarded client addresses |

Do not set wildcard hosts or origins. For remote administration, prefer the SSH
tunnel described below. Internet-facing deployments require HTTPS and an
explicit reverse-proxy policy.

### Database and authorization

| Variable | Local behavior | Purpose |
| --- | --- | --- |
| `VIBECANVAS_APP_PASSWORD` | generated | Password for the RLS-bound application role |
| `VIBECANVAS_MIGRATOR_PASSWORD` | generated | Password for the schema migration role |
| `VIBECANVAS_MAINTENANCE_PASSWORD` | generated | Password for bounded maintenance jobs |
| `OPENFGA_API_TOKEN` | generated | Authentication token for the private OpenFGA service |
| `OPENFGA_STORE_ID` | bootstrapped | Fixed production authorization store identifier |
| `OPENFGA_AUTHORIZATION_MODEL_ID` | bootstrapped | Fixed production authorization model identifier |
| `RUN_DATABASE_MIGRATIONS` | `false` | Keeps DDL in the one-shot migration workload instead of API/worker startup |

The generated passwords and tokens are secrets. Do not reuse one value for
multiple roles and do not commit the resulting environment file.

### Authentication and browser integration

| Variable | Default | Purpose |
| --- | ---: | --- |
| `WEB_SESSION_COOKIE_ENABLED` | `true` | Uses the HttpOnly browser session cookie flow |
| `WEB_SESSION_COOKIE_SECURE` | inferred | Forces Secure cookies; required for production HTTPS |
| `ENTERPRISE_SSO_ENABLED` | `false` | Enables configured enterprise OIDC login |
| `ENABLE_TEST_USER` | `false` | Enables the shared development test identity; never enable in production |
| `EXTENSION_SCOPED_TOKEN_ENABLED` | `true` | Enables audience-bound Chrome extension sessions |
| `VIBECANVAS_BROWSER_EXTENSION_ID` | project default | Exact published extension identity accepted by the API |
| `BROWSER_TOKEN_SECRET` | generated | HMAC key for short-lived browser WebSocket capabilities |

### Models, Agent Runtimes, and MCP

| Variable | Default | Purpose |
| --- | --- | --- |
| `VIBECANVAS_DEFAULT_AGENT_API` | empty | Optional JSON model profile used as the platform default; user-saved providers and Codex account login need no value here |
| `VIBECANVAS_DISABLE_PLATFORM_DEFAULT_API` | `0` | Ignores all operator-provided default model profiles when set to `1` |
| `AGENT_RUNTIME_TYPES` | `langchain,codex` | Runtime types exposed by the deployment |
| `CODEX_RUNTIME_AUTH_METHODS` | `chatgpt,managed_api,personal_api` | Codex connection methods shown to users |
| `CODEX_MANAGED_APIS_JSON` | `[]` | Operator-managed Codex profiles; API keys in this JSON are secrets |
| `RUNTIME_MODEL_CAPABILITY_TTL_S` | `7200` | Lifetime in seconds of a Chat/Turn-scoped model-broker capability |
| `PLATFORM_MCP_INTERNAL_BASE_URL` | installer-managed | Private API origin for Platform MCP and the model broker; Compose injects `http://api:8000`, while native setup uses loopback |
| `MCP_HANDSHAKE_TIMEOUT_S` | `60` | Maximum initialization time for one MCP server |
| `PLATFORM_MCP_CAPABILITY_TTL_S` | `86400` | Maximum lifetime of a scoped Platform MCP capability |

Leave `PLATFORM_MCP_INTERNAL_BASE_URL` empty when using the provided installers.
The Docker service name is not valid for native installation, and sandbox
loopback belongs to the sandbox itself rather than the API container.

### Storage and encryption

| Variable | Default | Purpose |
| --- | --- | --- |
| `OBJECT_STORE_PROVIDER` | `filesystem` | `filesystem` for single-node installs, `s3` for production, or `inmemory` for isolated tests only |
| `OBJECT_STORE_FS_ROOT` | managed volume | Encrypted filesystem object-store root shared by backend services |
| `S3_BUCKET` / `S3_REGION` | empty | Production S3 location when `OBJECT_STORE_PROVIDER=s3` |
| `S3_ENDPOINT_URL` | empty | Optional S3-compatible endpoint; leave blank for AWS S3 |
| `KMS_PROVIDER` | `local` | Envelope-encryption provider; production should use a workload-identity KMS |
| `KMS_LOCAL_MASTER_KEY` | generated | Local-only master key; sensitive and required to decrypt existing local data |
| `CONTENT_LOOKUP_HMAC_KEY` | generated | Stable key for protected content lookup identifiers |
| `MOUNT_PATH` | empty | Optional trusted host directory exposed as each user's isolated `/mount` tree |

Changing encryption keys after data is written makes existing ciphertext
unreadable. Back up the environment secret material separately from the data.

### Sandbox service and lifecycle

| Variable | Default | Purpose |
| --- | --- | --- |
| `SANDBOX_TYPE` | Docker: `rootful-snapshot` | Selects privilege and lifecycle behavior; see the lifecycle table below |
| `SANDBOX_GVISOR_PLATFORM` | Docker: `ptrace` | gVisor execution platform; use `systrap` only after a native host probe succeeds |
| `SANDBOX_NETWORK` | Docker: `none` | Network posture of the credential-free worker eligible for snapshots |
| `SANDBOX_EGRESS_MODE` | Docker: `proxy` | `proxy` preserves snapshot-compatible `network=none` while relaying controlled web traffic; `host-network` is an explicit trusted-development override |
| `SANDBOX_EGRESS_POLICY` | `public` | Public access policy shared by every sandbox: `public`, `allowlist`, or `platform-only` |
| `SANDBOX_EGRESS_ALLOW_HOSTS` | empty | Comma-separated public hosts/suffixes required by `allowlist` policy |
| `SANDBOX_EGRESS_PRIVATE_TARGETS` | empty | Trusted intranet web endpoints as exact `host:port` pairs; never use CIDR or wildcard private grants |
| `SANDBOX_EGRESS_TRUSTED_PROXY_CIDRS` | empty | Optional synthetic DNS ranges for advanced proxy environments |
| `SANDBOX_SERVICE_SOCKET` | `/run/vibecanvas-sandbox/sandboxd.sock` | Same-node private control socket used by API and workers |
| `SANDBOX_SERVICE_ENDPOINT` | empty | Cross-node sandboxd endpoint; requires `grpcs://` and workload certificates |
| `SANDBOX_PREWARM_ON_START` | `1` | Requires a real sandbox startup probe before application readiness |
| `SANDBOX_MAX_MOUNTS` | `24` | Hard mount-count ceiling for one sandbox |

### Observability

| Variable | Default | Purpose |
| --- | --- | --- |
| `LOG_LEVEL` | `INFO` | Backend log threshold |
| `LOG_FORMAT` | `json` | `json` for structured deployment logs or `console` for local reading |
| `METRICS_ENABLED` | `true` | Enables application and worker metrics |
| `OTEL_TRACES_ENABLED` | `false` | Enables OpenTelemetry export when an endpoint is also configured |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | empty | Private OTLP collector endpoint |
| `OTEL_SERVICE_NAME` | `vibecanvas-api` | Service identity attached to exported telemetry |

Logs and traces can contain identifiers and operational metadata. Keep metrics
and collector endpoints private and apply the same retention policy used for
other production audit data.

### Sandbox lifecycle settings

`SANDBOX_TYPE` is the sole privilege/lifecycle selector. Supported values are
`rootless-warm`, `rootful-warm`, and `rootful-snapshot`. Snapshot mode uses
these bounded startup settings:

| Variable | Default | Meaning |
| --- | ---: | --- |
| `SANDBOX_WARM_IDLE_TTL_S` | `300` | Idle seconds before an interactive Chat or Workflow-page Debug sandbox hibernates |
| `SANDBOX_SNAPSHOT_IDLE_TTL_S` | `1800` | Seconds a hibernated interactive snapshot remains before full release |
| `SANDBOX_ACTIVITY_POLL_INTERVAL_S` | `5` | How often sandboxd observes in-sandbox activity and advances lifecycle maintenance |
| `SANDBOX_WORKFLOW_SNAPSHOT_TTL_S` | `86400` | Cache lifetime for the credential-free baseline used by one-shot background runs |
| `SANDBOX_SNAPSHOT_CHECKPOINT_TIMEOUT_S` | `120` | Maximum checkpoint duration |
| `SANDBOX_SNAPSHOT_RESTORE_TIMEOUT_S` | `120` | Maximum restore/probe duration |
| `SANDBOX_SNAPSHOT_MAX_COUNT` | `128` | Maximum snapshot directories owned by sandboxd |
| `SANDBOX_SNAPSHOT_MAX_BYTES` | `21474836480` | Maximum aggregate snapshot bytes |
| `SANDBOX_MAX_MOUNTS` | `24` | Hard OCI mount-count ceiling per sandbox |

Docker deployments default `SANDBOX_GVISOR_PLATFORM` to `ptrace`, the portable
baseline that also works under Docker Desktop/WSL2. A native Linux operator may
explicitly select `systrap` after the startup capability probe succeeds on that
kernel and seccomp profile. The probe and all later sandbox launches use the
same configured platform; no automatic fallback silently changes the runtime
isolation profile.

Snapshot mode also defaults `SANDBOX_NETWORK=none`: gVisor cannot checkpoint a
container that uses hostinet. This setting governs the credential-free
file/workflow worker that is actually checkpointed. The active per-Chat Agent
Runtime is never checkpointed: sandboxd stops its MCP sessions and network
sockets before hibernation, then snapshots only the credential-free worker.
Docker Compose defaults `SANDBOX_EGRESS_MODE=proxy`, so both Workflow model/HTTP
calls and the active per-Chat Agent Runtime retain controlled web access while
the checkpointable worker remains `network=none`. An operator may explicitly
select `host-network` for trusted development or tools that require arbitrary
non-web protocols. That mode temporarily shares sandboxd's Docker network and
therefore exposes every destination reachable by sandboxd.

Production should use `SANDBOX_EGRESS_MODE=proxy`. The active Runtime remains
`network=none`, but a per-Chat loopback proxy and private UDS broker carry
HTTP(S) and WebSocket traffic for model calls, remote MCP servers, browser
automation, package downloads, and ordinary web tools. The broker automatically
grants only the exact private Platform MCP/model origin; database, Redis, and
authorization subnets are not exposed. `SANDBOX_EGRESS_POLICY=public`
allows arbitrary public destinations after DNS and private-address validation,
which is the practical default for a general-purpose Agent. Operators can use
`allowlist` for bounded workloads or `platform-only` for offline tasks. Tools
that require arbitrary non-HTTP TCP/UDP protocols need `host-network` or a
deployment-specific network-layer egress gateway.

#### Sandbox network modes

`SANDBOX_EGRESS_MODE` selects the network transport for every live sandbox.
It is independent from `SANDBOX_NETWORK`, which is explained below.

| `SANDBOX_EGRESS_MODE` | Public Internet | Platform MCP/model broker | Other private/intranet services | Protocols | Recommended use |
| --- | --- | --- | --- | --- | --- |
| `host-network` | Any destination reachable by `sandboxd` | Reachable through the Compose/internal service address | Any destination reachable by `sandboxd`, including attached Compose networks | General TCP/UDP, subject to gVisor and host support | Trusted development, or tools that genuinely require non-web protocols |
| `proxy` | Controlled by `SANDBOX_EGRESS_POLICY` | Automatically allowed only at its exact configured `host:port` | Denied by default; exact targets may be added explicitly | HTTP, HTTPS, and WebSocket through the sandbox proxy | Production and multi-user installations |

In `proxy` mode, `SANDBOX_EGRESS_POLICY` controls public destinations identically
for Chat, Workflow, MCP, Skill, batch, schedule and deployment sandboxes:

| Policy | Arbitrary public hosts | Listed public hosts | Platform MCP/model broker | Extra private targets | Typical workload |
| --- | --- | --- | --- | --- | --- |
| `public` | Allowed after DNS resolves exclusively to public addresses | Allowed | Allowed automatically | Exact `SANDBOX_EGRESS_PRIVATE_TARGETS` only | General-purpose agents and workflows |
| `allowlist` | Denied | Exact hosts and leading-dot suffixes in `SANDBOX_EGRESS_ALLOW_HOSTS`, plus validated job-declared hosts | Allowed automatically | Exact `SANDBOX_EGRESS_PRIVATE_TARGETS` only | Bounded enterprise deployments |
| `platform-only` | Denied | Denied | Allowed automatically | Exact `SANDBOX_EGRESS_PRIVATE_TARGETS` only | Offline/private deployments using platform-hosted capabilities |

For most installations, use `proxy` with the `public` policy. Choose
`allowlist` when destinations are known in advance, or `host-network` when a
tool requires non-HTTP protocols. The Platform MCP/model-broker address is
allowed automatically; other private services require an exact `host:port`.
Advanced proxy compatibility is handled by the Docker launcher.

`SANDBOX_NETWORK` has a narrower purpose. It configures the credential-free
file/workflow worker that may be checkpointed in `rootful-snapshot` mode. Keep
it at `none` for snapshot compatibility. The live Agent Runtime is stopped
before checkpointing and uses `SANDBOX_EGRESS_MODE` while it is active, so
`SANDBOX_NETWORK=none` does **not** mean that the Agent cannot call models or
tools.

The two interactive TTLs are sequential durations: five idle minutes in Warm,
then thirty minutes hibernated. Explicit UI release bypasses both. Batch,
schedule, webhook, and deployment runs are one-shot and are destroyed as soon
as execution finishes; only their clean baseline cache uses the Workflow TTL.
All values are validated at startup.

The sandbox does not own a TTL countdown. Its existing `serve-parallel` loop
publishes credential-free activity state (`active_jobs`, an activity sequence,
and the monotonic start of the current idle period). `sandboxd` observes that
state, maintains the authoritative elapsed-silence clock, and performs
hibernate/release transitions. API clients receive elapsed time and the active
phase TTL and may derive a display countdown, but they never control release.

## Remote access

Services bind to loopback by default. For a remote host, prefer an SSH tunnel:

```bash
ssh -N -L 9001:127.0.0.1:9001 user@server
```

Then open <http://localhost:9001> locally.

## Production deployment

Do not expose the development stack directly to the Internet. A production
deployment should provide:

- HTTPS termination at a trusted reverse proxy
- strict host and origin allowlists
- externally managed, rotated secrets
- S3-compatible encrypted object storage
- proxy-enforced sandbox egress restrictions
- backups for PostgreSQL and object storage
- private metrics and OpenFGA endpoints
- vulnerability-scanned, digest-pinned container images

The production release script intentionally requires reviewed evidence and
attested images. See `scripts/deploy/production_release.sh --help` and the
security workflows under `.github/workflows/` before promotion.

## Troubleshooting

Inspect status and logs first:

```bash
./scripts/deploy/local_server.sh status
./scripts/deploy/local_server.sh logs api
./scripts/deploy/local_server.sh logs sandboxd
```

Validate resolved Compose configuration without starting:

```bash
./scripts/deploy/local_server.sh config
```

Run the local deployment verifier:

```bash
./scripts/deploy/local_server.sh verify
```
