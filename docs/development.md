# Development Guide

This guide covers the local environment and checks used to contribute to
Skeinix. For normal installation and day-to-day operation, follow the
[installation guide](installation.md) instead.

## Python environment

Create the repository-local, pinned Python environment and install the locked
dependencies:

```bash
uv python install 3.11.15
uv venv --python 3.11.15 --seed .venv
uv pip install --python .venv/bin/python --require-hashes --requirement requirements-build.txt
uv pip install --python .venv/bin/python --requirement requirements-dev.txt
uv pip install --python .venv/bin/python --no-build-isolation --no-deps --editable ./engine --editable ./api
source .venv/bin/activate
```

Run the Python test suites:

```bash
python -m pytest engine/tests api/tests
```

## Web application

Install, lint, test, and build the web application with the pinned package
manager and lockfile:

```bash
cd web
corepack enable
pnpm install --frozen-lockfile
pnpm lint
pnpm test
pnpm build
```

## Browser end-to-end tests

Browser end-to-end tests require a running local stack:

```bash
cd web
pnpm test:e2e
```

See [CONTRIBUTING.md](../CONTRIBUTING.md) for dependency updates, pull request
expectations, code standards, and the complete recommended check list.
