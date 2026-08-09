import { expect, test, type Page } from '@playwright/test';

import {
  createWorkflow,
  deleteWorkflow,
  seedAuthAndLocale,
} from './fixtures';

const MISSING_UUID = '00000000-0000-4000-8000-000000000001';

async function expectRouteSurface(page: Page, path: string) {
  await expect(page).toHaveURL(new RegExp(`${path.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}(?:[?#]|$)`));
  await expect(page.locator('#root')).toBeVisible();
  const busy = page.locator('[aria-busy="true"]');
  if (await busy.count()) await expect(busy.first()).toBeHidden({ timeout: 20_000 });
  await expect(page.locator('body')).not.toContainText(
    /Unexpected Application Error|Application crashed/i,
  );
}

test.describe('dynamic route navigation continuity', () => {
  let workflowId = '';

  test.beforeAll(async () => {
    workflowId = await createWorkflow(`navigation-continuity-${Date.now()}`);
  });

  test.afterAll(async () => {
    if (workflowId) await deleteWorkflow(workflowId);
  });

  test.beforeEach(async ({ context }) => {
    await seedAuthAndLocale(context, 'en');
  });

  test('direct loads every dynamic route and preserves history, refresh, and new tabs', async ({
    context,
    page,
  }) => {
    test.setTimeout(180_000);
    const deepRoutes = [
      `/tasks/${MISSING_UUID}`,
      `/deployments/${MISSING_UUID}`,
      '/mcp-servers/discover/e2e-missing-source',
      '/mcp-servers/platform/browser',
      `/mcp-servers/${MISSING_UUID}`,
      '/skills/discover/e2e-missing-source',
      `/skills/${MISSING_UUID}`,
      `/knowledge/${MISSING_UUID}`,
      `/workflow/${workflowId}`,
      `/workflow/${workflowId}/version/v1.sv0`,
    ];

    for (const path of deepRoutes) {
      await page.goto(path, { waitUntil: 'domcontentloaded' });
      await expectRouteSurface(page, path);
    }

    const workflowPath = `/workflow/${workflowId}`;
    await page.goto(workflowPath, { waitUntil: 'domcontentloaded' });
    await expectRouteSurface(page, workflowPath);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expectRouteSurface(page, workflowPath);

    await page.goto('/workspace', { waitUntil: 'domcontentloaded' });
    await page.goto(workflowPath, { waitUntil: 'domcontentloaded' });
    await page.goto('/settings', { waitUntil: 'domcontentloaded' });
    await page.goBack({ waitUntil: 'domcontentloaded' });
    await expectRouteSurface(page, workflowPath);
    await page.goBack({ waitUntil: 'domcontentloaded' });
    await expectRouteSurface(page, '/workspace');
    await page.goForward({ waitUntil: 'domcontentloaded' });
    await expectRouteSurface(page, workflowPath);

    const deepTab = await context.newPage();
    await deepTab.goto(`/workflow/${workflowId}/version/v1.sv0`, {
      waitUntil: 'domcontentloaded',
    });
    await expectRouteSurface(deepTab, `/workflow/${workflowId}/version/v1.sv0`);
    await deepTab.close();
  });
});
