# Skeinix Web

The Web package is the React application for the Skeinix workspace, workflow
canvas, agent chat, deployments, tasks, settings, and administrative surfaces.

## Stack

- React 19, TypeScript, Vite, and React Router
- TanStack Query and Zustand
- React Flow for the workflow canvas
- Tailwind CSS and Radix UI primitives
- Vitest, Testing Library, MSW, Playwright, and axe-core

## Development

Install the pinned package manager and dependencies:

```bash
corepack enable
pnpm install --frozen-lockfile
pnpm dev
```

The Vite development server proxies API requests to the local backend. For a
complete environment, use the repository-level launcher described in
[`docs/installation.md`](../docs/installation.md).

## Verification

```bash
pnpm lint
pnpm test
pnpm build
```

End-to-end tests require a running local stack:

```bash
pnpm test:e2e
```

The committed `openapi.json` snapshot and generated
`src/lib/api/schema.d.ts` keep builds independent of a live backend. After an
API schema change, update both with:

```bash
pnpm codegen:snapshot
```

## Deployment paths

The application supports both origin-root and reverse-proxy path-prefix
deployments. Runtime coordinates may be supplied through
`window.__VIBECANVAS_RUNTIME_CONFIG__`; build-time defaults use
`VITE_APP_BASE_PATH` and `VITE_API_BASE`.

Never place secrets in variables prefixed with `VITE_`: Vite embeds them in
browser assets. Configure allowed development hosts with `WEB_ALLOWED_HOSTS`
using exact hosts or reviewed suffixes such as `.example.test`.

## Container image

Build from the repository root:

```bash
cd /path/to/Skeinix
docker build -f web/Dockerfile -t skeinix-web:dev .
```

The image serves the built SPA through nginx and proxies API and streaming
requests to the API service.

## Directory layout

```text
e2e/       Playwright end-to-end specifications
public/    Static public assets
scripts/   Code generation, audits, and browser verification
src/app/   Router, providers, layout, and error boundaries
src/components/ Shared UI components
src/lib/   API clients, streaming, i18n, and utilities
src/pages/ Route-level product surfaces
src/stores/ Client state
```

## License

Apache-2.0. See [`LICENSE`](../LICENSE).
