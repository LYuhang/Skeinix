/**
 * E2E: workflow card → kebab menu → Delete → typed-name confirmation.
 *
 * Critical-journey G13 spec #5. Verifies:
 *   - Pre-seeded workflow appears in /workspace
 *   - Card kebab menu surfaces a Delete action
 *   - Delete dialog requires typing the exact workflow name to enable
 *     the destructive button (GitHub-pattern guard)
 *   - Confirmation deletes the workflow + the card disappears
 *
 * The pre-seed uses the API directly so the spec doesn't depend on
 * spec #2 (test isolation matters; specs must be runnable individually).
 */
import { test, expect } from '@playwright/test';
import {
  createWorkflow,
  deleteWorkflow,
  seedAuthAndLocale,
} from './fixtures';

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context);
});

test('typed-name confirmation deletes a workflow', async ({ page }) => {
  const name = `e2e-delete-${Date.now()}`;
  const wfId = await createWorkflow(name);

  try {
    await page.goto('/workspace');
    await expect(
      page.getByRole('heading', { name: /workflows/i }),
    ).toBeVisible();

    // The card by accessible name = workflow_name. We use a regex anchored
    // to the timestamp to avoid matching unrelated cards in a dirty test DB.
    const card = page.getByText(name, { exact: true }).first();
    await expect(card).toBeVisible({ timeout: 10_000 });

    // Open the per-card kebab menu — the aria-label embeds the workflow
    // name, so we target it by accessible role + the name.
    await page
      .getByRole('button', { name: new RegExp(`actions menu for ${name}`, 'i') })
      .click();
    await page.getByRole('menuitem', { name: /delete/i }).click();

    // Destructive confirm — button is disabled until the name matches.
    const dialog = page.getByRole('dialog');
    await expect(dialog).toBeVisible();
    const deleteBtn = dialog.getByRole('button', { name: 'Delete' });
    await expect(deleteBtn).toBeDisabled();

    await dialog.getByLabel(/type/i).fill(name);
    await expect(deleteBtn).toBeEnabled();

    await deleteBtn.click();

    // The entire workflow row disappears. Scoping by its stable identity
    // avoids ambiguity now that both the title and Open action are links.
    await expect(
      page.locator(`[data-testid="wf-row"][data-wf-id="${wfId}"]`),
    ).toHaveCount(0, { timeout: 10_000 });
  } finally {
    // Idempotent — no-op if the UI delete already succeeded.
    await deleteWorkflow(wfId);
  }
});
