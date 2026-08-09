/**
 * Playwright E2E configuration.
 *
 * The default config boots API + Web for product journeys. Standalone browser
 * gates that own their fixtures (for example the unpacked MV3 extension test)
 * set `VIBECANVAS_SKIP_WEB_SERVER=1` so they do not allocate unrelated services.
 *
 * Why `reuseExistingServer: !process.env.CI`: locally a dev contributor
 * may already have `pnpm dev` running; Playwright will reuse it rather
 * than failing on the port collision. In CI we always boot fresh so the
 * run is hermetic.
 */
import { defineConfig } from '@playwright/test';

const API_PORT = Number(process.env.VIBECANVAS_API_PORT ?? 8000);
const WEB_PORT = Number(process.env.VIBECANVAS_WEB_PORT ?? 5173);
const E2E_HOST = process.env.VIBECANVAS_E2E_HOST ?? '127.0.0.1';
const API_PYTHON = process.env.VIBECANVAS_PYTHON ?? 'python';
const SKIP_WEB_SERVER = process.env.VIBECANVAS_SKIP_WEB_SERVER === '1';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: `http://${E2E_HOST}:${WEB_PORT}`,
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },
  webServer: SKIP_WEB_SERVER ? undefined : [
    {
      // Use the same explicit Python as the native stack when provided. A
      // generic system `python` frequently lacks the editable API package.
      command: `"${API_PYTHON}" -m uvicorn vibecanvas_api.app:build_app --factory --host ${E2E_HOST} --port ${API_PORT}`,
      cwd: '../api',
      url: `http://${E2E_HOST}:${API_PORT}/healthz`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `pnpm dev --host ${E2E_HOST} --port ${WEB_PORT}`,
      url: `http://${E2E_HOST}:${WEB_PORT}/`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
});
