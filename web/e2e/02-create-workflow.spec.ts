/**
 * E2E: workspace → new-workflow modal → navigate to canvas.
 *
 * Critical-journey G13 spec #2 — exercises the WorkspacePage CRUD modal
 * and the workspace → canvas route handoff (mutation success closes the
 * modal and `useNavigate('/workflow/{wf_id}')` lands the user on a
 * fresh canvas). Verifies:
 *   - "+ New Workflow" opens the modal
 *   - Form submission posts to the API and navigates to /workflow/{wfId}
 *   - The canvas page renders (toolbar visible) before this test exits
 *
 * Cleanup deletes the created workflow against the backend so the
 * fixture doesn't bleed into spec #5.
 */
import { test, expect } from '@playwright/test';
import { deleteWorkflow, seedAuthAndLocale } from './fixtures';

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context);
});

test('opens the new-workflow modal, submits, and lands on the canvas', async ({
  page,
}) => {
  // Per-test name so concurrent CI runs don't collide on the list view.
  const name = `e2e-create-${Date.now()}`;

  await page.goto('/workspace');
  await expect(
    page.getByRole('heading', { name: 'Workflows', exact: true }),
  ).toBeVisible();

  await page.getByRole('button', { name: /new workflow/i }).click();
  // Modal is portalled — assert by role rather than child query.
  await expect(page.getByRole('dialog')).toBeVisible();

  await page.getByTestId('create-workflow-name').fill(name);
  await page.getByRole('button', { name: 'Create' }).click();

  // Modal closes + navigation to /workflow/{wf_id}.
  await expect(page).toHaveURL(/\/workflow\/[a-f0-9]{12}$/);
  const createdWfId = page.url().match(/\/workflow\/([a-f0-9]{12})$/)?.[1] ?? null;
  expect(createdWfId, 'parsed wf_id from URL').not.toBeNull();

  // The canvas toolbar's Save button is the stable landmark — it
  // mounts after `useWorkflow` resolves and is unique on the page.
  await expect(
    page.locator('[data-action="canvas-save"]'),
  ).toBeVisible({ timeout: 10_000 });

  // Cleanup at the API level — UI delete is covered by spec #5.
  if (createdWfId) await deleteWorkflow(createdWfId);
});
