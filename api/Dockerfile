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

# Codex App Server is an external runtime dependency, not a Python package.
# Extract the reviewed platform-native bundle in a disposable Node stage so
# the final API image contains neither Node.js nor npm.
FROM node:22.23.2-alpine3.23@sha256:46825fbbd4e996a78b7a2cdc08d75e38a5a505bdab95dcda55605359bf124bc6 AS codex-cli
ARG CODEX_CLI_VERSION
ARG NPM_VERSION
ARG TARGETARCH
RUN set -eux; \
    npm install --global --ignore-scripts --no-audit --no-fund "npm@${NPM_VERSION}"; \
    test "$(npm --version)" = "${NPM_VERSION}"; \
    npm install --global --ignore-scripts --no-audit --no-fund \
        "@openai/codex@${CODEX_CLI_VERSION}"; \
    case "${TARGETARCH:-$(apk --print-arch)}" in \
        amd64|x86_64) package_arch="x64"; target="x86_64-unknown-linux-musl" ;; \
        arm64|aarch64) package_arch="arm64"; target="aarch64-unknown-linux-musl" ;; \
        *) echo "Unsupported Codex CLI architecture: ${TARGETARCH:-unknown}" >&2; exit 1 ;; \
    esac; \
    vendor="/usr/local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-${package_arch}/vendor/${target}"; \
    test -x "${vendor}/bin/codex"; \
    mkdir -p /opt/codex; \
    cp -a "${vendor}/." /opt/codex/; \
    test "$(/opt/codex/bin/codex --version)" = "codex-cli ${CODEX_CLI_VERSION}"

FROM python:3.11.15-slim-trixie@sha256:90744cff8f32887f075c47d747a173ff333e9e98801667af93c357fa9f5e28ff
ARG CODEX_CLI_VERSION

WORKDIR /app

COPY --from=codex-cli /opt/codex /opt/codex
RUN ln -s /opt/codex/bin/codex /usr/local/bin/codex && \
    test "$(codex --version)" = "codex-cli ${CODEX_CLI_VERSION}"

# gVisor (runsc) — workflow execution is sandbox-ONLY since P2 (no in-process
# fallback), so the runtime image MUST carry the runsc binary. Fetch the SAME
# pinned, sha512-verified release as scripts/get_runsc.sh (keep the pin in sync)
# to /usr/local/bin/runsc (on PATH → resolved by which(runsc); RUNSC_PATH in
# compose makes it explicit). The image is shared by the unprivileged API/worker
# services and the dedicated rootful sandboxd service. Only sandboxd launches
# runsc and receives the required host capabilities; its startup probe validates
# the selected platform plus checkpoint/restore support on the actual host.
ARG RUNSC_RELEASE=20260601.0
RUN set -eux; \
    apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates \
        fonts-dejavu-core=2.37-8 \
        fonts-wqy-zenhei=0.9.45-8; \
    arch="$(uname -m)"; \
    base="https://storage.googleapis.com/gvisor/releases/release/${RUNSC_RELEASE}/${arch}"; \
    curl -fsSL --retry 3 -o /tmp/runsc "${base}/runsc"; \
    curl -fsSL --retry 3 -o /tmp/runsc.sha512 "${base}/runsc.sha512"; \
    echo "$(awk '{print $1}' /tmp/runsc.sha512 | head -n1)  /tmp/runsc" | sha512sum -c -; \
    chmod 0755 /tmp/runsc && mv /tmp/runsc /usr/local/bin/runsc; \
    rm -f /tmp/runsc.sha512; \
    apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Install reviewed, hash-locked build tooling and runtime dependencies before
# local source. Local packages disable build isolation so no undeclared package
# downloads can occur during their wheel builds.
COPY requirements-build.txt /tmp/requirements-build.txt
COPY requirements-runtime.txt /tmp/requirements-runtime.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements-build.txt && \
    pip install --no-cache-dir --require-hashes -r /tmp/requirements-runtime.txt

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

# Package installers are build tools, not runtime dependencies. Removing them
# from the final image reduces its attack surface without changing application
# imports or the hash-locked dependency set above.
RUN python -m pip uninstall -y \
      pip setuptools wheel hatchling editables pathspec pluggy trove-classifiers \
      jaraco.context && \
    test ! -e /usr/local/bin/pip && \
    test ! -e /usr/local/bin/pip3

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
ENV VIBECANVAS_API_CORS_ORIGINS=http://localhost:3000
USER 10001:10001

# Production-ready default. Override VIBECANVAS_API_DEV_TOKEN at run time.
CMD ["uvicorn", "vibecanvas_api.app:build_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
