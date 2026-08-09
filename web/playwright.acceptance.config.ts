/**
 * Playwright config for the 2026-06-09 workflow E2E ACCEPTANCE suite.
 *
 * Unlike the base `playwright.config.ts`, this config does NOT boot its own
 * `webServer` — the acceptance pass runs against an already-up native stack
 * (`bash scripts/native_dev_up.sh up`, web on :5173 in preview mode, api on
 * :8000). It only drives the live browser; the stack lifecycle is owned by
 * the runbook script, not Playwright.
 *
 * `testDir` is scoped to `e2e/acceptance` so this config runs only the new
 * acceptance specs, leaving the legacy journey specs to the base config.
 */
import { defineConfig } from '@playwright/test';

const WEB_PORT = Number(process.env.VIBECANVAS_WEB_PORT ?? 5173);

export default defineConfig({
  testDir: './e2e/acceptance',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  reporter: 'list',
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
});
