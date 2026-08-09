# Skeinix API

The API package contains Skeinix's FastAPI service, agent runtime, persistence,
authorization, background jobs, sandbox control plane, and deployment APIs. Its
Python import name remains `vibecanvas_api` for compatibility.

## Requirements

- Python 3.11.15 (managed by uv in the supported development setup)
- PostgreSQL
- Redis
- OpenFGA for the full authorization profile
- the sibling `engine/` package

For a complete environment, follow the repository-level
[installation guide](../docs/installation.md). To install only the Python
packages in the repository-local uv environment:

```bash
cd ..
uv python install 3.11.15
uv venv --python 3.11.15 --seed .venv
uv pip install --python .venv/bin/python --requirement requirements-dev.txt
uv pip install --python .venv/bin/python --no-deps --editable ./engine --editable ./api
source .venv/bin/activate
```

## Run the service

Copy the public example configuration and review its values:

```bash
cp .env.example .env
vibecanvas-api serve --host 127.0.0.1 --port 8000
```

The full stack normally starts through the repository launcher or Docker
Compose, which also provides migrations, workers, authorization, and sandbox
services. Do not expose a development server directly to the Internet.

Useful endpoints on a local server:

- `GET /healthz` — liveness
- `GET /docs` — Swagger UI
- `GET /redoc` — ReDoc
- `GET /openapi.json` — OpenAPI schema

Run migrations manually when needed:

```bash
alembic upgrade head
```

## Tests

The test suite uses local PostgreSQL server binaries through
`pytest-postgresql`; it does not require a long-running development database.

```bash
python -m pytest api/tests
```

Some integration tests additionally require gVisor, browser binaries, or a
running stack and skip when their prerequisites are unavailable.

## Container image

Use the repository root as the build context so the engine package is included:

```bash
cd /path/to/Skeinix
docker build -f api/Dockerfile -t skeinix-api:dev .
```

Production images must be digest-pinned and attested as described in
[`DEPLOY.md`](../DEPLOY.md).

## Package boundaries

- `routes/` defines HTTP contracts.
- `storage/` contains PostgreSQL repositories and models.
- `authorization/` owns the OpenFGA model and adapters.
- `agents/` and `services/agent_runtime/` implement agent execution.
- `services/sandbox/` owns isolated execution contracts and brokers.
- `celery_tasks/` contains asynchronous workers.
- `security/` contains security profiles, migrations, and verification logic.

The workflow execution primitives belong in `engine/`; UI behavior belongs in
`web/`. Keep credentials out of source and pass them through the documented
runtime configuration paths.

## License

Apache-2.0. See [`LICENSE`](../LICENSE).
