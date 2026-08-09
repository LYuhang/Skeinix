# Contributing to Skeinix

Thank you for helping improve Skeinix. This project welcomes bug fixes,
documentation improvements, tests, security hardening, and focused features.

## Before you start

- Search existing issues and pull requests.
- Open a discussion or issue before a large architectural change.
- Never include credentials, private data, production exports, or internal URLs.
- Keep public-facing documentation and code comments in English.

## Development setup

Follow the dedicated [development guide](docs/development.md). Use reproducible
dependency installs and keep the checked-in lockfiles unchanged unless the
dependency set is intentionally updated.

### Dependency updates

Python development dependencies are pinned in `requirements-dev.txt`. Runtime
and build-tool dependencies are reviewed, hash-locked subsets in
`requirements-runtime.txt`, `requirements-build.txt`, and their Engine
counterparts. Regenerate them with the exact commands recorded at the top of
each input/lock file, then run:

```bash
.venv/bin/python scripts/verify_dependency_locks.py
.venv/bin/python -m pip check
```

Commit the relevant `pyproject.toml`, input file, and generated lock file in the
same pull request. Do not hand-edit generated locks. For JavaScript,
update `package.json` and `pnpm-lock.yaml` together and always install with
`--frozen-lockfile` outside an intentional dependency update.

## Pull requests

1. Create a focused branch from the current default branch.
2. Add or update tests for behavior changes.
3. Run the relevant checks locally.
4. Update documentation when configuration or public behavior changes.
5. Explain the motivation, implementation, risk, and verification in the PR.

Recommended checks:

```bash
source .venv/bin/activate
python -m pytest engine/tests api/tests
pnpm --dir web lint
pnpm --dir web test
pnpm --dir web build
pnpm --dir extension test
pnpm --dir extension build
```

Run browser E2E tests when changing user-visible flows:

```bash
pnpm --dir web test:e2e
```

Tests that access a public package registry are opt-in:

```bash
SKEINIX_TEST_NETWORK=1 python -m pytest -m gvisor \
  api/tests/test_agent_workspace_e2e.py
```

## Code expectations

- Preserve module boundaries between `engine`, `api`, `web`, and `extension`.
- Prefer explicit schemas and typed interfaces at trust boundaries.
- Do not bypass authentication, authorization, sandboxing, or validation to make
  a test pass.
- Keep secrets server-side. Browser build variables are public.
- Avoid unrelated formatting or refactoring in focused fixes.
- Add comments for non-obvious constraints, not for self-evident syntax.

## Commit style

Use short imperative subjects. Conventional Commit prefixes are encouraged:

```text
feat: add workflow export validation
fix(api): reject expired browser capabilities
docs: clarify native installation
test(engine): cover parallel branch failure
```

## Security issues

Do not open public issues for vulnerabilities. Follow [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions are licensed under the
Apache License 2.0.
