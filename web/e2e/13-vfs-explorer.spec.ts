/**
 * E2E: VFS 2c Workflow Explorer (versions + files).
 *
 * Smoke for the left Explorer added in VFS 2c: toggle it open from the
 * canvas toolbar and confirm its two sections render — Versions (a freshly
 * created workflow always has a `v1.sv0` HEAD) and Files (the agent writes
 * artifacts/scratch here; a brand-new workflow shows the empty states).
 *
 * Like the rest of `e2e/`, this needs the real stack booted by
 * `playwright.config.ts`'s `webServer` (backend `uvicorn` + `pnpm dev`).
 * In sandboxes where the backend stack can't start (no persistent Postgres
 * / conda env off PATH), run it out-of-band — it is NOT part of the vitest
 * unit gate. There is no VFS write API (the agent owns VFS writes via
 * tools), so this smoke asserts the Explorer shell + the Versions row that
 * exists for every workflow, not seeded artifact content.
 */
import { test, expect } from '@playwright/test';
import { seedAuthAndLocale } from './fixtures';
import { createWorkflow, deleteWorkflow } from './fixtures';

let wfId: string;

test.beforeAll(async () => {
  wfId = await createWorkflow('vfs-explorer-smoke');
});

test.afterAll(async () => {
  await deleteWorkflow(wfId);
});

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context);
});

test('toggles the Workflow Explorer and shows Versions, Nodes, and Sandbox', async ({ page }) => {
  await page.goto(`/workflow/${wfId}`);

  // The Explorer is default-collapsed; open it from the toolbar "Files" toggle.
  await page.locator('[data-action="files"]').click();

  const versions = page.getByRole('button', { name: 'Workflow Versions' });
  await expect(versions).toBeVisible();
  await expect(page.getByRole('button', { name: 'Nodes' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sandbox', exact: true })).toBeVisible();

  // Every workflow has a v1.sv0 HEAD — the Versions section lists it.
  await versions.click();
  await expect(page.getByText('v1.sv0')).toBeVisible();

  // Collapse it again via the in-rail chevron.
  await page.locator('[data-action="explorer-collapse"]').click();
  await expect(versions).toHaveCount(0);
});
