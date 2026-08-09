/**
 * E2E: accessibility scans on the two largest surfaces (G14).
 *
 * Uses `@axe-core/playwright` to flag WCAG 2 / Section 508 violations
 * after the page is fully loaded. We assert zero violations of impact
 * `serious` or above; minor issues are reported but don't fail the
 * test (they accumulate in `axe-results-*.json` if the reporter is
 * configured).
 *
 * Two journeys:
 *   1. `/workspace` — landing page; covers card list + header.
 *   2. `/workflow/{wfId}` — the canvas page; covers the xyflow host,
 *      toolbar, agent chat sidebar (with empty session list), and
 *      right inspector tabs.
 *
 * Together these exercise every shared component (Button, Input,
 * Dialog, Tab, ResizablePanel, Card, Tooltip) on the two routes a
 * non-power user spends ~100% of their time in.
 */
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import {
  createWorkflow,
  deleteWorkflow,
  seedAuthAndLocale,
  seedStartNode,
} from './fixtures';

const FAIL_AT_OR_ABOVE: Array<'serious' | 'critical'> = ['serious', 'critical'];
const MISSING_UUID = '00000000-0000-4000-8000-000000000001';

async function seriousAxeViolations(page: import('@playwright/test').Page) {
  const builder = new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']);
  if (page.url().includes('/workflow/')) {
    builder.exclude('.react-flow__renderer');
  }
  const results = await builder.analyze();
  return results.violations.filter((violation) =>
    FAIL_AT_OR_ABOVE.includes(violation.impact as 'serious' | 'critical'),
  );
}

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context);
});

test('workspace passes axe scan (serious+ violations)', async ({ page }) => {
  await page.goto('/workspace');
  await expect(
    page.getByRole('heading', { name: /workflows/i }),
  ).toBeVisible();

  const serious = await seriousAxeViolations(page);
  // Surface the offenders verbatim so a failing CI run is debuggable
  // without trace re-runs.
  expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
});

test('canvas + chat sidebar pass axe scan (serious+ violations)', async ({
  page,
}) => {
  const wfId = await createWorkflow(`e2e-axe-${Date.now()}`);
  await seedStartNode(wfId);
  try {
    await page.goto(`/workflow/${wfId}`);
    await expect(page.locator('[data-action="canvas-save"]')).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.getByRole('region', { name: 'Workflow canvas' })).toBeVisible();

    const serious = await seriousAxeViolations(page);
    expect(serious, JSON.stringify(serious, null, 2)).toEqual([]);
  } finally {
    await deleteWorkflow(wfId);
  }
});

test('all production page families and durable error states pass axe', async ({ page, context }) => {
  test.setTimeout(180_000);
  const wfId = await createWorkflow(`e2e-axe-matrix-${Date.now()}`);
  await seedStartNode(wfId);
  const authRoutes = [
    '/login',
    '/signup',
    '/reset-password',
  ];
  const authenticatedRoutes = [
    '/chat',
    '/workspace',
    '/tasks',
    `/tasks/${MISSING_UUID}`,
    '/deployments',
    `/deployments/${MISSING_UUID}`,
    '/credentials',
    '/mcp-servers',
    '/mcp-servers/platform/browser',
    '/mcp-servers/discover/e2e-missing-source',
    '/skills',
    '/skills/discover/e2e-missing-source',
    '/knowledge',
    `/knowledge/${MISSING_UUID}`,
    '/storage',
    `/workflow/${wfId}`,
    `/workflow/${wfId}/version/v1.sv0`,
    '/settings',
    '/settings?tab=organization',
    '/management',
    '/embed/chat',
  ];

  try {
    // The suite-level fixture starts authenticated. Clear only cookies here so
    // auth routes are scanned as their real signed-out pages rather than a
    // redirect landing page, then restore the same Secure Cookie fixture for
    // protected production routes.
    await context.clearCookies();
    for (const route of authRoutes) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      await expect(page, `${route}: remained on signed-out surface`).toHaveURL(
        new RegExp(`${route.replace('/', '\\/')}(?:\\?|$)`),
      );
      await expect(page.getByRole('main'), `${route}: main landmark`).toBeVisible();
      await expect(page.getByRole('heading', { level: 1 }), `${route}: page title`).toHaveCount(1);
      const serious = await seriousAxeViolations(page);
      expect(serious, `${route}\n${JSON.stringify(serious, null, 2)}`).toEqual([]);
    }

    await seedAuthAndLocale(context);
    for (const route of authenticatedRoutes) {
      await page.goto(route, { waitUntil: 'domcontentloaded' });
      await expect(page.locator('#root'), `${route}: root surface`).toBeVisible();
      const busy = page.locator('[aria-busy="true"]');
      if (await busy.count()) {
        await expect(busy.first(), `${route}: loading state`).toBeHidden({ timeout: 10_000 });
      }
      if (route === '/chat') {
        await expect(page.getByRole('log', { name: 'Conversation' })).toBeVisible();
      }
      if (route === `/workflow/${wfId}`) {
        await expect(page.getByRole('region', { name: 'Workflow canvas' })).toBeVisible();
      }
      const serious = await seriousAxeViolations(page);
      expect(serious, `${route}\n${JSON.stringify(serious, null, 2)}`).toEqual([]);
    }
  } finally {
    await deleteWorkflow(wfId);
  }
});
