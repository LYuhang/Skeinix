"""Static lint of api/Dockerfile (Gate 4, real docker build is local-only)."""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE_PATH = Path(__file__).resolve().parents[1] / "Dockerfile"
API_ROOT = DOCKERFILE_PATH.parent


def _dockerfile_instructions() -> list[str]:
    text = DOCKERFILE_PATH.read_text()
    return [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def test_dockerfile_exists():
    assert DOCKERFILE_PATH.is_file()


def test_dockerfile_uses_python_3_10_or_later_base():
    lines = _dockerfile_instructions()
    from_lines = [ln for ln in lines if ln.upper().startswith("FROM ")]
    assert len(from_lines) == 2, f"expected Codex and Python stages, got {from_lines}"
    assert from_lines[0].startswith("FROM node:22.23.2-bookworm-slim@sha256:")
    m = re.match(r"FROM\s+python:3\.(\d+)([-\w.]*)?@sha256:", from_lines[-1])
    assert m, (
        f"final stage must use a digest-pinned python:3.x base, got: {from_lines[-1]}"
    )
    minor = int(m.group(1))
    assert minor >= 10


def test_dockerfile_pins_and_verifies_external_runtime_assets():
    text = DOCKERFILE_PATH.read_text()
    assert "ARG CODEX_CLI_VERSION=0.147.0" in text
    assert "ARG NPM_VERSION=11.19.0" in text
    assert 'test "$(npm --version)" = "${NPM_VERSION}"' in text
    assert '"@openai/codex@${CODEX_CLI_VERSION}"' in text
    assert 'test "$(codex --version)" = "codex-cli ${CODEX_CLI_VERSION}"' in text
    assert "api/elk-runtime/package-lock.json" in text
    assert "npm ci --prefix /opt/elk" in text
    assert "COPY --from=runtime-assets /usr/local/bin/node" in text
    assert "VIBECANVAS_ELK_LAYOUT_SCRIPT=/opt/elk/elk-layout.mjs" in text
    assert '"engineVersion":"elkjs-0.12.0"' in text
    assert "ARG RUNSC_RELEASE=20260601.0" in text
    assert "sha512sum -c -" in text
    assert "fonts-dejavu-core=2.37-8" in text
    assert "fonts-wqy-zenhei=0.9.45-8" in text
    assert "USER 10001:10001" in text


def test_dockerfile_copies_pyproject_and_src():
    text = DOCKERFILE_PATH.read_text()
    assert "pyproject.toml" in text, "Dockerfile must COPY pyproject.toml"
    assert re.search(r"\bsrc/?\b", text), "Dockerfile must COPY src/"


def test_dockerfile_runs_pip_install():
    text = DOCKERFILE_PATH.read_text()
    assert re.search(r"pip\s+install\b", text), (
        "Dockerfile must RUN pip install to materialize the package"
    )
    assert "requirements-build.txt" in text
    assert text.count("--require-hashes") >= 2
    assert text.count("--no-build-isolation") >= 2
    assert "pip uninstall -y" in text
    assert "hatchling editables" in text


def test_dockerfile_installs_shared_sandbox_python_baseline():
    text = DOCKERFILE_PATH.read_text()
    assert "COPY requirements-sandbox.txt" in text
    assert "--require-hashes -r /tmp/requirements-sandbox.txt" in text
