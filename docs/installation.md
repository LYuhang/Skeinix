# Installation

Skeinix supports two local installation methods. Docker Compose is recommended
for evaluation and self-hosted use. The native Linux setup is intended for
contributors who need to work directly with the source code.

| Method | Recommended for | Host requirements |
| --- | --- | --- |
| **Docker Compose** | Evaluation and local self-hosting | Docker Engine or Docker Desktop with Compose v2 |
| **Native Linux / WSL** | Development and debugging | Debian, Ubuntu, or WSL with `apt`, `sudo`, and network access |

Production deployments have different security and release requirements. Do
not expose the local stack directly to the Internet; use the
[production deployment guide](../DEPLOY.md) instead.

## Docker Compose

### Prerequisites

Install the following tools before starting:

- Git;
- Docker Engine or Docker Desktop;
- Docker Compose v2;
- OpenSSL and `curl`.

Four CPU cores and 8 GiB of memory are recommended for a complete local stack.
The host must support privileged Linux containers and the gVisor startup probe.
Compatibility is verified automatically before the API begins accepting
traffic.

Docker installation does not require Python, Node.js, or pnpm on the host. All
application dependencies are built into the project images.

### Install and start

Clone the repository and start the stack:

```bash
git clone https://github.com/LYuhang/Skeinix.git
cd Skeinix
./scripts/deploy/local_server.sh up
```

On the first run, the launcher:

1. creates `.env` from `.env.example`;
2. generates independent local passwords and encryption keys;
3. restricts `.env` permissions to `0600`;
4. validates Docker, Compose, the network binding, and required settings;
5. builds and starts the services;
6. applies database migrations and initializes OpenFGA; and
7. verifies the Web, API, `sandboxd`, and gVisor checkpoint/restore path.

The first build can take several minutes. When verification succeeds, open
<http://localhost:9001>.

The supported entry point is
[`local_server.sh`](../scripts/deploy/local_server.sh). It operates the service
topology defined in [`docker-compose.yml`](../docker-compose.yml); individual
Dockerfiles are build inputs, not separate installation procedures.

### Configure before the first start

The default command is sufficient for a localhost installation. To review or
change settings before any service starts, initialize the environment file
separately:

```bash
./scripts/deploy/local_server.sh init
```

Edit `.env`, then start the stack:

```bash
./scripts/deploy/local_server.sh up
```

If `.env` already exists, the initializer preserves its contents. Do not delete
or regenerate an environment file after storing data: it contains encryption
keys required to read the existing object store.

### Manage the stack

| Action | Command |
| --- | --- |
| Start or apply local changes | `./scripts/deploy/local_server.sh up` |
| Stop and remove containers | `./scripts/deploy/local_server.sh stop` |
| Restart containers | `./scripts/deploy/local_server.sh restart` |
| Show service status | `./scripts/deploy/local_server.sh status` |
| Follow all logs | `./scripts/deploy/local_server.sh logs` |
| Follow one service | `./scripts/deploy/local_server.sh logs api` |
| Run deployment verification | `./scripts/deploy/local_server.sh verify` |
| Validate the resolved Compose file | `./scripts/deploy/local_server.sh config` |
| Run prerequisites only | `./scripts/deploy/local_server.sh preflight` |

Stopping the stack does not remove its named volumes. The database, object
store, runtime files, and generated `.env` remain available for the next start.

### Docker Desktop and WSL2

When Docker Desktop provides the daemon through WSL2 integration, use that
daemon directly. Do not install a second Docker daemon inside the WSL
distribution: the two daemons use different sockets, images, and volumes.

It is normal for `systemctl is-active docker` to report an inactive service in
this configuration. `docker info` must still succeed from the shell where
Skeinix is started. The startup probe determines whether the Docker Desktop
kernel supports the required gVisor mode.

## Native Linux or WSL

The native installer supports Debian, Ubuntu, and WSL distributions that use
`apt`. Run it as a normal login user. The script invokes `sudo` only when it
installs operating-system packages; do not run the entire script as root.

### Install and start

Clone the repository, then run the bootstrap:

```bash
git clone https://github.com/LYuhang/Skeinix.git
cd Skeinix
./scripts/bootstrap_native_linux.sh
```

The bootstrap installs the required system packages, Node.js, pnpm, Codex CLI,
uv, Python dependencies, frontend dependencies, and the pinned gVisor runtime.
It then creates the local configuration and starts Skeinix.

To prepare the environment without starting services:

```bash
./scripts/bootstrap_native_linux.sh --prepare-only
```

Start the prepared installation later with:

```bash
./launch.sh start
```

The implementation is available in
[`bootstrap_native_linux.sh`](../scripts/bootstrap_native_linux.sh) and
[`launch.sh`](../launch.sh).

### Local files

Native installation keeps its dependencies and configuration outside the
system Python environment:

| Path | Purpose |
| --- | --- |
| `.venv/` | Repository-local Python environment managed by uv |
| `.env.launch.local` | Local listener and feature configuration; mode `0600` |
| `~/.vibecanvas/` | Persistent local secrets and runtime data |
| `/tmp/vibecanvas-native/` | Process identifiers and logs by default |

These repository-local files are ignored by Git. Do not point
`VIBECANVAS_PYTHON` to Conda or a system interpreter; the launcher expects the
environment created under this checkout.

Set `VIBECANVAS_NATIVE_RUNTIME_DIR` before running `launch.sh` if process state
and logs should be stored somewhere other than `/tmp/vibecanvas-native`.

### Manage the native stack

| Action | Command |
| --- | --- |
| Start | `./launch.sh start` |
| Stop | `./launch.sh stop` |
| Restart | `./launch.sh restart` |
| Show status | `./launch.sh status` |
| Show recent logs | `./launch.sh logs` |

For manual dependency installation, test commands, and frontend development,
continue with the [development guide](development.md).

## First-run setup

After the stack is healthy:

1. open <http://localhost:9001>;
2. create a user account or sign in;
3. open **Settings → Runtime**;
4. add a supported model provider or connect a Codex account; and
5. start a Chat or create a Workflow.

A model provider is not required for the platform to start, but Agent Chat
cannot run until a model connection is available. User-managed providers can be
configured from Settings; a deployment-wide default is optional.

### Install the Browser Extension

Each deployment builds an extension package for its configured public URL. In
Skeinix, open **Settings → Extensions → Download extension**, then:

1. extract the ZIP to a permanent folder;
2. open `chrome://extensions`;
3. enable **Developer mode**;
4. choose **Load unpacked** and select the extracted folder; and
5. pin Skeinix and open its side panel.

If the deployment URL changes, rebuild or restart the deployment and download
the extension again. The allowed Web origin is compiled into the extension
package.

## Common configuration

Docker reads `.env`; native installation reads `.env.launch.local`. Restart the
affected services after changing either file. The full configuration template
is documented inline in [`.env.example`](../.env.example).

The following settings are the ones most commonly changed for a local
installation:

| Variable | Local default | Purpose |
| --- | --- | --- |
| `VIBECANVAS_PUBLIC_URL` | `http://localhost:9001` | Browser-visible application URL used by links, authentication callbacks, and the extension build |
| `VIBECANVAS_BIND_ADDRESS` | `127.0.0.1` | Address used for published Docker ports; keep loopback unless direct LAN access is intentional |
| `VIBECANVAS_HTTP_PORT` | `9001` | Web application port |
| `VIBECANVAS_API_PORT` | `8000` | Direct API port used for health checks and development diagnostics |
| `OBJECT_STORE_PROVIDER` | `filesystem` | Local file-backed object storage; production deployments normally use `s3` |
| `SANDBOX_EGRESS_MODE` | `proxy` | Routes sandbox HTTP(S) and WebSocket traffic through the controlled egress proxy |
| `SANDBOX_EGRESS_POLICY` | `public` | Controls whether sandboxes may reach public destinations, an allowlist, or platform services only |
| `MOUNT_PATH` | (empty string) | Optional trusted host directory exposed through each user's isolated `/mount` path |

Keep generated passwords, signing keys, browser token secrets, and encryption
keys out of Git, shell history, logs, and issue reports. Back up secret material
separately from persistent data. Production secret management, storage, and
egress requirements are covered in [Production deployment](../DEPLOY.md).

For the design behind the sandbox modes and network controls, see
[Sandbox lifecycle](architecture.md#sandbox-lifecycle) and
[Network boundaries](architecture.md#network-boundaries).

## Remote access

Services bind to loopback by default. For a remote server, the simplest and
safest access method is an SSH tunnel:

```bash
ssh -N -L 9001:127.0.0.1:9001 user@server
```

Keep the command running and open <http://localhost:9001> on the local machine.
No listener or origin setting needs to change for this method.

For direct access over a trusted LAN, bind Docker to one exact private address
and set `VIBECANVAS_PUBLIC_URL` to the matching origin. Do not use `0.0.0.0` or
`::`; the local preflight check rejects wildcard bindings. Native deployments
must also list the exact host and browser origin in `.env.launch.local`.

Internet-facing access requires HTTPS, a trusted reverse proxy, explicit host
and origin allowlists, firewall policy, and production secret management. Use
the [production deployment process](../DEPLOY.md) instead of modifying the local
stack for this purpose.

## Updating a source installation

Skeinix is currently in alpha. Review release notes and back up PostgreSQL,
object storage, and the environment secret file before updating.

For Docker Compose:

```bash
git pull --ff-only
./scripts/deploy/local_server.sh up
```

The launcher rebuilds changed images, applies migrations, and verifies the
resulting stack.

For a native installation:

```bash
git pull --ff-only
./scripts/bootstrap_native_linux.sh --prepare-only
./launch.sh restart
```

Production systems must use verified release images and the upgrade procedure
in [Production deployment](../DEPLOY.md), not a source checkout update.

## Troubleshooting

### Start with status and logs

For Docker Compose:

```bash
./scripts/deploy/local_server.sh status
./scripts/deploy/local_server.sh logs api
./scripts/deploy/local_server.sh logs sandboxd
```

For a native installation:

```bash
./launch.sh status
./launch.sh logs
```

### Run the Docker checks separately

Use the preflight command to check Docker, Compose, generated secrets, network
binding, and the resolved Compose file without starting services:

```bash
./scripts/deploy/local_server.sh preflight
```

Use the verifier to check health endpoints, the private `sandboxd` socket, and
the gVisor checkpoint/restore path:

```bash
./scripts/deploy/local_server.sh verify
```

The verification steps are implemented in
[`preflight.sh`](../scripts/deploy/preflight.sh) and
[`verify_local.sh`](../scripts/deploy/verify_local.sh).

### Common failures

| Symptom | Recommended action |
| --- | --- |
| `Docker daemon is unavailable` | Run `docker info`. Start Docker Engine or enable Docker Desktop integration for the current WSL distribution. |
| `Docker Compose v2 plugin is unavailable` | Install or update the Compose v2 plugin; the legacy `docker-compose` command is not supported. |
| A service port is already in use | Change the corresponding `VIBECANVAS_*_PORT` value in `.env`. When changing the Web port, update `VIBECANVAS_PUBLIC_URL` as well. |
| `sandbox_prewarm` or checkpoint/restore fails | Inspect the `sandboxd` logs. Confirm that the active Docker kernel permits privileged containers and the configured gVisor platform. |
| Native startup reports a missing `.venv` | Run `./scripts/bootstrap_native_linux.sh --prepare-only`, then retry. |
| The application opens but Chat cannot start | Configure a model under **Settings → Runtime**, then inspect the API logs for provider or credential errors. |
| The extension cannot connect | Download the package from the current deployment again and confirm that `VIBECANVAS_PUBLIC_URL` matches the URL opened in Chrome. |
| Account deletion reports an OpenFGA erasure configuration error | For custom infrastructure, provision the scoped OpenFGA change-feed erasure function and set `OPENFGA_ERASURE_DATABASE_URL`. Compose and the native launcher configure it automatically. See [Security and data lifecycle](security-and-data-lifecycle.md#account-deletion). |

Do not resolve an encryption or authentication error by replacing `.env` or
`.env.launch.local`; doing so can make existing encrypted data inaccessible.

## Next steps

- Learn how the services fit together in [Architecture](architecture.md).
- Prepare a hardened deployment with [Production deployment](../DEPLOY.md).
- Set up tests and development tools with the [Development guide](development.md).
- Review data protection and retention in
  [Security and data lifecycle](security-and-data-lifecycle.md).
