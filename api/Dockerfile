# Skeinix API data plane and FastAPI service container.
#
# Build:
#   cd /path/to/Skeinix
#   docker build -f api/Dockerfile -t vibecanvas-api:dev .
#
# Run:
#   docker run --rm -p 8000:8000 \
#     -e VIBECANVAS_API_DEV_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(24))') \
#     -e VIBECANVAS_API_CORS_ORIGINS=http://localhost:3000 \
#     vibecanvas-api:dev
#
# Build context is the repository root (NOT api/) so the Dockerfile can COPY
# the engine package from ../engine.

ARG CODEX_CLI_VERSION=0.147.0
ARG NPM_VERSION=11.19.0

# Codex App Server and the pinned ELK worker are external runtime dependencies,
# not Python packages. Build both in one disposable, digest-pinned stage. The
# final image receives the Codex native bundle, the Node executable, and only
# elkjs (not npm or the frontend dependency tree).
FROM node:22.23.2-bookworm-slim@sha256:d649c27dae7ba0137b3cef5dd75baa422c08dc3d9e3fc0c23dfb172dc3cc6436 AS runtime-assets
ARG CODEX_CLI_VERSION
ARG NPM_VERSION
ARG TARGETARCH
RUN set -eux; \
    npm install --global --ignore-scripts --no-audit --no-fund "npm@${NPM_VERSION}"; \
    test "$(npm --version)" = "${NPM_VERSION}"; \
    npm install --global --ignore-scripts --no-audit --no-fund \
        "@openai/codex@${CODEX_CLI_VERSION}"; \
    case "${TARGETARCH:-$(dpkg --print-architecture)}" in \
        amd64|x86_64) package_arch="x64"; target="x86_64-unknown-linux-musl" ;; \
        arm64|aarch64) package_arch="arm64"; target="aarch64-unknown-linux-musl" ;; \
        *) echo "Unsupported Codex CLI architecture: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    vendor="/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-${package_arch}/vendor/${target}"; \
    test -x "${vendor}/bin/codex"; \
    mkdir -p /opt/codex; \
    cp -a "${vendor}/." /opt/codex/; \
    test "$(/opt/codex/bin/codex --version)" = "codex-cli ${CODEX_CLI_VERSION}"

COPY api/elk-runtime/package.json api/elk-runtime/package-lock.json /opt/elk/
RUN npm ci --prefix /opt/elk --ignore-scripts --no-audit --no-fund \
        --omit=dev && \
    test "$(node -p 'require("/opt/elk/node_modules/elkjs/package.json").version')" = "0.12.0"
COPY web/scripts/elk-layout.mjs /opt/elk/elk-layout.mjs
RUN printf '%s' \
      '{"graph":{"id":"image-probe","children":[{"id":"a","width":80,"height":40}]}}' \
      | node /opt/elk/elk-layout.mjs \
      | grep -q '"engineVersion":"elkjs-0.12.0"'

FROM python:3.11.15-slim-trixie@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff
ARG CODEX_CLI_VERSION

WORKDIR /app

COPY --from=runtime-assets /opt/codex /opt/codex
COPY --from=runtime-assets /usr/local/bin/node /usr/local/bin/node
COPY --from=runtime-assets /opt/elk /opt/elk
RUN ln -s /opt/codex/bin/codex /usr/local/bin/codex && \
    test "$(codex --version)" = "codex-cli ${CODEX_CLI_VERSION}" && \
    test "$(node --version)" = "v22.23.2" && \
    printf '%s' \
      '{"graph":{"id":"image-probe","children":[{"id":"a","width":80,"height":40}]}}' \
      | node /opt/elk/elk-layout.mjs \
      | grep -q '"engineVersion":"elkjs-0.12.0"'

# gVisor (runsc) — workflow execution is sandbox-ONLY since P2 (no in-process
# fallback), so the runtime image MUST carry the runsc binary. Fetch the SAME
# pinned, sha512-verified release as scripts/get_runsc.sh (keep the pin in sync)
# to /usr/local/bin/runsc (on PATH → resolved by which(runsc); RUNSC_PATH in
# compose makes it explicit). The image is shared by the unprivileged API/worker
# services and the dedicated rootful sandboxd service. Only sandboxd launches
# runsc and receives the required host capabilities; its startup probe validates
# the selected platform plus checkpoint/restore support on the actual host.
ARG RUNSC_RELEASE=20260601.0
# Small, version-pinned command-line baseline exposed inside every gVisor
# sandbox. These are runtime inspection/editing tools; compilers and language
# package managers remain opt-in environment dependencies to keep the trusted
# base image lean and its attack surface bounded.
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates=20250419 \
        curl=8.14.1-2+deb13u4 \
        file=1:5.46-5 \
        git=1:2.47.3-0+deb13u1 \
        jq=1.7.1-6+deb13u3 \
        openssh-client=1:10.0p1-7+deb13u4 \
        patch=2.8-2 \
        procps=2:4.0.4-9 \
        ripgrep=14.1.1-1+b4 \
        unzip=6.0-29 \
        zip=3.0-15 \
        fonts-dejavu-core=2.37-8 \
        fonts-wqy-zenhei=0.9.45-8; \
    arch="$(uname -m)"; \
    base="https://storage.googleapis.com/gvisor/releases/release/${RUNSC_RELEASE}/${arch}"; \
    curl -fsSL --retry 3 -o /tmp/runsc "${base}/runsc"; \
    curl -fsSL --retry 3 -o /tmp/runsc.sha512 "${base}/runsc.sha512"; \
    echo "$(awk '{print $1}' /tmp/runsc.sha512 | head -n1)  /tmp/runsc" | sha512sum -c -; \
    chmod 0755 /tmp/runsc && mv /tmp/runsc /usr/local/bin/runsc; \
    rm -f /tmp/runsc.sha512; \
    rm -rf /var/lib/apt/lists/*

# Install reviewed, hash-locked build tooling and runtime dependencies before
# local source. Local packages disable build isolation so no undeclared package
# downloads can occur during their wheel builds.
COPY requirements-build.txt /tmp/requirements-build.txt
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
COPY requirements-sandbox.txt /tmp/requirements-sandbox.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-build.txt && \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements-runtime.txt && \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements-sandbox.txt

# Engine is installed from this repository without resolving dependencies again.
COPY engine /tmp/engine
RUN pip install --no-cache-dir --no-deps --no-build-isolation /tmp/engine

# api package
COPY api/pyproject.toml api/README.md /app/
COPY api/src/ /app/src/

# Alembic migrations (required for entrypoint `alembic upgrade head`)
COPY api/alembic.ini /app/alembic.ini
COPY api/alembic/ /app/alembic/
# Strict cutover commands are deployment assets, not Runtime compatibility
# readers. They run before the application image is admitted.
COPY scripts/security/ /app/scripts/security/

RUN pip install --no-cache-dir --no-deps --no-build-isolation .

# API/worker images do not need a package installer. The dedicated sandboxd
# image opts into pip because it owns the narrow, wheel-only, content-addressed
# Workflow dependency builder; third-party code still executes only in gVisor.
ARG VIBECANVAS_RUNTIME_ENV_BUILDER=0
RUN if [ "${VIBECANVAS_RUNTIME_ENV_BUILDER}" = "1" ]; then \
      python -m pip --version; \
    else \
      python -m pip uninstall -y \
        pip setuptools wheel hatchling editables pathspec pluggy trove-classifiers \
        jaraco.context && \
      test ! -e /usr/local/bin/pip && \
      test ! -e /usr/local/bin/pip3; \
    fi

# Every API-derived service is unprivileged by default. Compose explicitly
# overrides only sandboxd to UID 0 for rootful gVisor checkpoint/restore.
RUN groupadd --gid 10001 vibecanvas && \
    useradd --uid 10001 --gid 10001 --create-home \
      --home-dir /home/vibecanvas --shell /usr/sbin/nologin vibecanvas && \
    install -d -o 10001 -g 10001 -m 0770 \
      /var/lib/celerybeat \
      /var/lib/vibecanvas/local-data \
      /var/lib/vibecanvas/objectstore \
      /var/lib/vibecanvas/lib-overlay \
      /var/lib/vibecanvas/agent-runtime \
      /var/lib/vibecanvas/vfs-volumes \
      /run/vibecanvas-openfga \
      /run/vibecanvas-sandbox

EXPOSE 8000
ENV VIBECANVAS_API_CORS_ORIGINS=http://localhost:3000 \
    VIBECANVAS_ELK_LAYOUT_SCRIPT=/opt/elk/elk-layout.mjs
USER 10001:10001

# Production-ready default. Override VIBECANVAS_API_DEV_TOKEN at run time.
CMD ["uvicorn", "vibecanvas_api.app:build_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
