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

#### Fresh Ubuntu server

On a fresh Ubuntu host, install Docker Engine and Compose from Docker's official
APT repository. Follow the current [Docker Engine installation guide](https://docs.docker.com/engine/install/ubuntu/),
including its repository-signing and package-installation steps. Ensure the
login user can run `docker info` and `docker compose version` without `sudo`
before starting Skeinix; after adding the user to the `docker` group, sign out
and start a new SSH session so the membership takes effect.

For Ubuntu 24.04 (Noble) on amd64, the repository also provides
[`install_docker_ubuntu.sh`](../scripts/install_docker_ubuntu.sh). After cloning
Skeinix, run the script as the normal login user; it configures Docker's signed
APT repository, installs Engine, Buildx, and Compose, and verifies a rootful
daemon. Start a new login session before continuing with the commands below.

For a long first build over SSH, use a persistent terminal such as `tmux` or
`screen`, or arrange for the command to continue after a connection loss. The
launcher is idempotent and can be run again if an interrupted client did not
leave a build process running.

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

#### Remote server deployment

The public URL is the only browser-facing build parameter. The launcher derives
the canonical Host and CORS Origin, enables secure cookies for HTTPS, and
compiles the same URL into the extension package. On a cloud VM, also provide
the private interface address used behind the provider's public-IP NAT:

```bash
./scripts/deploy/local_server.sh up \
  --public-url https://skeinix.example.com \
  --bind-address 10.0.0.4
```

The command persists the derived settings in `.env`; subsequent `up` and
`restart` commands do not need the options. Passing a new `--public-url` updates
the derived settings without regenerating secrets or persistent data. Because
the origin is compiled into the MV3 manifest, download the extension again
after changing the URL.

Point the domain's DNS record at the public IP and terminate HTTPS at a trusted
reverse proxy. Forward the proxy to the private Web entry point on port `9001`.
For example, a minimal Caddy site block is:

```caddyfile
skeinix.example.com {
  reverse_proxy 10.0.0.4:9001
}
```

Allow inbound TCP `80` for certificate issuance and redirect, and `443` for
application traffic. Do not expose `8000`, `5432`, `6379`, `8080`, `2112`, or
`9100`; the launcher keeps these control-plane ports on loopback. After HTTPS is
working, the cloud firewall does not need to expose `9001` publicly.

For short-lived evaluation without a purchased domain, an IP-encoded hostname
such as `203-0-113-20.sslip.io` can resolve to the corresponding public IP. This
depends on the third-party [sslip.io service](https://sslip.io/) and is not a
substitute for deployment-owned DNS.

Plain HTTP by public IP can evaluate the main application, but Chrome will not
retain the extension's partitioned session on an insecure public origin.
Extension sign-in, WebAuthn, passkeys, and other secure-context browser features
require trusted HTTPS outside `localhost`. Do not weaken the extension cookie
attributes to bypass this browser boundary. Production deployments have
additional infrastructure requirements described in the
[production deployment guide](../DEPLOY.md).

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
3. open **Settings → Agent Runtime**;
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

Docker reads `.env`; native installation reads `.env.launch.local`. For Docker,
prefer the deployment options over editing related variables individually:

| Deployment input | Local default | Purpose |
| --- | --- | --- |
| `--public-url URL` | `http://localhost:9001` | Sets the browser-visible URL and derives Host, CORS, secure-cookie, and extension build settings |
| `--bind-address ADDRESS` | `127.0.0.1` | Publishes only the Web entry point on one exact host interface; use the VM private IP behind cloud NAT |

The derived values are persisted in `.env`. Most deployments should not set
`WEB_ALLOWED_HOSTS`, `VIBECANVAS_API_CORS_ORIGINS`,
`WEB_SESSION_COOKIE_SECURE`, `VIBECANVAS_EXTENSION_WEB_BASE`, or
`VIBECANVAS_EXTENSION_ALLOWED_ORIGINS` separately. They remain available as
advanced overrides for deployments with multiple reviewed public entries.

The following runtime settings are occasionally changed independently:

| Variable | Local default | Purpose |
| --- | --- | --- |
| `VIBECANVAS_HTTP_PORT` | `9001` | Web application port |
| `OBJECT_STORE_PROVIDER` | `filesystem` | Local file-backed object storage; production deployments normally use `s3` |
| `SANDBOX_EGRESS_MODE` | `proxy` | Routes sandbox HTTP(S) and WebSocket traffic through the controlled egress proxy |
| `SANDBOX_EGRESS_POLICY` | `public` | Controls whether sandboxes may reach public destinations, an allowlist, or platform services only |
| `MOUNT_PATH` | (empty string) | Optional trusted host directory exposed through each user's isolated `/mount` path |

Keep `VIBECANVAS_INTERNAL_BIND_ADDRESS` at its `127.0.0.1` default. It protects
API diagnostics, databases, authorization services, queues, and metrics from
being published with the Web entry point. The full advanced template is
documented inline in [`.env.example`](../.env.example). Restart the affected
services after changing configuration.

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

For direct access over a trusted LAN, pass the matching HTTP URL and one exact
private address to `--public-url` and `--bind-address`. Do not use `0.0.0.0` or
`::`; the launcher rejects wildcard bindings. Native deployments must list the
exact host and browser origin in `.env.launch.local`.

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
| The application opens but Chat cannot start | Configure a model under **Settings → Agent Runtime**, then inspect the API logs for provider or credential errors. |
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
