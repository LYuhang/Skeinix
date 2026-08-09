# Production deployment

This guide describes Skeinix's production release boundary. For local Docker
Compose and native development setup, start with
[`docs/installation.md`](docs/installation.md).

> [!WARNING]
> The default Compose stack is intended for local evaluation. Do not expose it
> directly to the Internet or treat locally built images as a production
> release.

## Production prerequisites

A production environment must provide:

- HTTPS termination through a trusted reverse proxy;
- externally managed and regularly rotated secrets;
- PostgreSQL backups with tested restoration procedures;
- encrypted S3-compatible object storage and lifecycle policies;
- private Valkey/Redis-compatible, OpenFGA, metrics, and administrative endpoints;
- enforced sandbox egress controls and a supported gVisor runtime;
- digest-pinned container images with verified provenance and SBOMs; and
- reviewed production evidence owned by people other than the submitter.

The API's production profile validates required security settings at startup
and fails closed when a required control is missing.

Use a rootful container host for production sandboxing. Keep the Web, API, and
worker containers unprivileged; only the dedicated `sandboxd` service receives
the host capabilities required by `runsc`. With
`SANDBOX_TYPE=rootful-snapshot`, startup performs a real checkpoint, restore,
and file-channel probe before API or worker traffic is admitted. A failed probe
stops deployment rather than silently switching sandbox modes.

`SANDBOX_GVISOR_PLATFORM` defaults to the portable `ptrace` baseline, including
under Docker Desktop/WSL2. A native Linux operator may explicitly select
`systrap` only after the same startup checkpoint/restore probe succeeds on the
target kernel and seccomp profile. The runtime never silently falls back to a
different platform.

`sandboxd` is also the lifecycle authority. The in-sandbox job server publishes
only positive activity facts (`active_jobs`, a sequence, and the monotonic start
of its idle period). `sandboxd` polls those facts, combines them with its own
operation leases, and measures elapsed silence before checkpointing or releasing.
API and Web clients may display a derived countdown but never trigger TTL expiry.

All API and worker replicas for a scope must route to the same owning
`sandboxd`. The bundled single-node deployment guarantees this with one private
Unix socket. Do not place a round-robin load balancer in front of multiple
`sandboxd` instances: distributed sandbox ownership and lease transfer are not
implemented. A multi-node deployment must shard scopes with sticky routing to a
single daemon endpoint.

Before selecting production TTLs, benchmark a real new-Chat mount and first tool
round-trip on the intended sandbox host:

```bash
sudo -E SKEINIX_RUN_ROOTFUL_GVISOR_BENCHMARK=1 \
  .venv/bin/pytest -q -s \
  api/tests/integration/test_chat_sandbox_startup_benchmark.py
```

The benchmark validates real `/data`, `/memory`, `/logs`, and `/mount` binds and
reports cold channel readiness, first-tool latency, checkpoint time, and restore
latency as JSON. Optional `SKEINIX_CHAT_SANDBOX_COLD_SLO_S` and
`SKEINIX_CHAT_SANDBOX_RESTORE_SLO_S` values turn the measurements into
host-specific deployment gates.

## Build and attest release images

The `.github/workflows/release-images.yml` workflow builds the API and Web
images from a protected version tag. It scans the images and attaches Sigstore
provenance and SPDX SBOM attestations.

Configure a protected `production-release` GitHub environment and require
reviewers before running release workflows. Record the exact repository,
40-character commit SHA, tag ref, and image digests used for deployment.

## Prepare deployment inputs

The release overlay is [`docker-compose.release.yml`](docker-compose.release.yml).
It accepts only prebuilt, digest-pinned images. Store the production environment
file and evidence manifest outside the repository with restrictive filesystem
permissions.

Example environment variables:

```bash
export VIBECANVAS_API_IMAGE='ghcr.io/owner/skeinix-api@sha256:...'
export VIBECANVAS_WEB_IMAGE='ghcr.io/owner/skeinix-web@sha256:...'
export RELEASE_REPOSITORY='owner/Skeinix'
export RELEASE_SHA='0123456789abcdef0123456789abcdef01234567'
export RELEASE_REF='refs/tags/v1.0.0'
export PRODUCTION_EVIDENCE_MANIFEST='/secure/skeinix/production-evidence.json'
export VIBECANVAS_ENV_FILE='/secure/skeinix/production.env'
```

Never place real values in shell history, documentation, issues, or repository
files. Prefer a deployment identity that reads them directly from a secret
manager.

## Verify and deploy

The supported production entry point is:

```bash
./scripts/deploy/production_release.sh up
```

The script verifies release attestations, validates the evidence manifest and
resolved Compose configuration, then deploys with
`--no-build --pull always`. It must not be replaced with a direct production
`docker compose up --build` command.

Inspect the release without applying it:

```bash
./scripts/deploy/production_release.sh config
```

## Post-deployment checks

After deployment:

1. Confirm every service is healthy and running the reviewed digest.
2. Verify the public health endpoint through the HTTPS reverse proxy.
3. Exercise authentication, authorization denial, sandbox execution, and audit
   delivery using dedicated non-production test identities.
4. Confirm backups and monitoring are active.
5. Retain the evidence manifest, image digests, source SHA, and deployment logs.

## Rollback

Rollback means redeploying a previously verified pair of API and Web image
digests with the matching evidence. Do not rebuild an old source checkout on
the production host. Database migrations may require a separate, reviewed
rollback or restore procedure; test that procedure before each release.

Report suspected security problems using [`SECURITY.md`](SECURITY.md).
