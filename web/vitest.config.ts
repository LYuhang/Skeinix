/**
 * Vitest configuration.
 *
 * Why a separate config (vs. merging into `vite.config.ts`):
 *   - Keeps the build/dev config free of test-only plugins and aliases.
 *   - Lets test files live alongside source (`__tests__/`) without
 *     polluting the production bundle graph.
 *
 * Notable choices:
 *   - `environment: 'jsdom'` — RTL needs a DOM. We pin `jsdom@29` (the
 *     legacy line that still works against Node 24 in this devbox).
 *   - `globals: false` — explicit `import { describe, it, expect } from
 *     'vitest';` plays nicely with `verbatimModuleSyntax` (tsconfig.app)
 *     and surfaces typos at compile time rather than at runtime.
 *   - `setupFiles` — wires `@testing-library/jest-dom` matchers + MSW
 *     server lifecycle. See `src/setup-tests.ts`.
 *   - Coverage scope is narrowed to `lib/` + `stores/` because those
 *     are the pure modules with a meaningful coverage signal. UI
 *     coverage is better measured via the E2E specs.
 *   - `e2e/` lives under `web/` but uses `@playwright/test`, not vitest.
 *     We explicitly exclude it to avoid double-runs and test-runner
 *     conflicts.
 *
 * Vitest 4 API parity: `defineConfig` from `vitest/config` accepts the
 * same shape as v2/v3 for our purposes; the only break we hit during
 * setup was the `coverage.include` glob being stricter about leading
 * `./` — we use the bare-relative form below.
 */
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'node:path';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  // `import.meta.env.VITE_API_BASE` is statically inlined by Vite's
  // transformer at compile time — `test.env` only updates `process.env`
  // and is too late for openapi-fetch's `new Request(baseUrl + path)`.
  // We define it at config level so the transformer emits the literal
  // `'http://localhost'` into the bundled client, giving openapi-fetch
  // an absolute baseUrl that Node's undici Request constructor accepts.
  define: {
    'import.meta.env.VITE_API_BASE': JSON.stringify('http://localhost'),
  },
  test: {
    environment: 'jsdom',
    globals: false,
    // ORDER MATTERS: setup-polyfills runs first to lift TransformStream
    // onto globalThis so MSW's `brotli-decompress` import doesn't crash
    // before `setup-tests.ts` reaches its msw-handlers import.
    setupFiles: ['./src/setup-polyfills.ts', './src/setup-tests.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    exclude: ['node_modules', 'dist', 'e2e', '.playwright-cli'],
    css: false,
    // openapi-fetch builds a `new Request()` from `baseUrl + path`. Under
    // jsdom + Node's undici, `new Request('/api/v1/...')` throws
    // "Invalid URL" because Node's URL constructor rejects bare paths
    // even though the browser would resolve them against `window.location`.
    // Stamping a synthetic absolute baseUrl here makes openapi-fetch emit
    // fully-qualified URLs that both Request and MSW v2 accept. MSW will
    // still intercept on the path-suffix match in our handlers.
    env: { VITE_API_BASE: 'http://localhost' },
    // Devbox note: the default `forks` pool spawns child processes via
    // IPC, and the sandbox here can't complete the worker handshake
    // (60s timeout). `threads` keeps everything in worker_threads
    // (shared memory, no fork IPC) and runs reliably here. Trade-off:
    // workers share heap, so a test that mutates Node globals will
    // leak across files — but our setup file resets MSW + Zustand
    // state per test, and we don't touch Node globals.
    //
    // Vitest 4 note: `test.poolOptions` was flattened in v4 — see
    // https://vitest.dev/guide/migration#pool-rework. `fileParallelism:
    // false` is the new top-level option that effectively forces a
    // single worker; setting `maxWorkers: 1` is the documented equivalent.
    pool: 'threads',
    fileParallelism: false,
    maxWorkers: 1,
    minWorkers: 1,
    // Keep each file's module graph isolated. Several component suites mock
    // different slices of @xyflow/react and API query modules; sharing those
    // factories makes the full run order-dependent even when Zustand/MSW state
    // itself is reset. The single-worker `threads` pool avoids child-process
    // IPC and, unlike vmThreads in Vitest 4, gives these Vite-transformed local
    // modules an independent mock registry per test file.
    isolate: true,
    teardownTimeout: 30_000,
    coverage: {
      provider: 'v8',
      include: ['src/lib/**', 'src/stores/**'],
      exclude: ['**/*.d.ts', '**/__tests__/**', '**/schema.d.ts'],
      reporter: ['text', 'html'],
    },
  },
});
