/**
 * E2E: dedicated MCP server management route.
 *
 * Why this is a smoke-only spec
 * -----------------------------
 * Spawning a real MCP server (e.g. `npx @modelcontextprotocol/server-everything`)
 * is out of scope for the regular Playwright suite — that fixture lands
 * in MCP T9's verification gates where the gate runner is willing to wait
 * on a 30-second subprocess. Here we restrict to UI plumbing:
 *
 *   1. `/mcp-servers` renders the installed-server surface.
 *   2. The Add button opens the wizard `Dialog` and step 0 inputs appear.
 *   3. Closing the wizard via Cancel restores the list view.
 *
 * The full happy path — Add → Test → Save against a live MCP server —
 * is owned by T9 (gate G3). See `mcp-servers.spec.ts.bak` placeholder in
 * the docs if you're looking for a starting point.
 */
import { test, expect } from '@playwright/test';
import { seedAuthAndLocale } from './fixtures';

test.describe('MCP servers settings — smoke', () => {
  test.beforeEach(async ({ context }) => {
    await seedAuthAndLocale(context, 'en');
  });

  test('opens settings → mcp tab and the Add wizard renders', async ({
    page,
  }) => {
    await page.goto('/mcp-servers');

    await expect(page).toHaveURL(/\/mcp-servers$/);
    await expect(page.getByRole('heading', { name: 'MCP Servers', exact: true })).toBeVisible();
    await expect(page.getByRole('tab', { name: /installed/i })).toHaveAttribute(
      'data-state',
      'active',
    );

    // Add button visible (T7 testid) — wired to the wizard in T8.
    const addBtn = page.getByTestId('mcp-add-button');
    await expect(addBtn).toBeVisible();
    await addBtn.click();

    // Step 0 inputs render.
    await expect(page.getByTestId('mcp-name')).toBeVisible();
    await expect(page.getByTestId('mcp-endpoint')).toBeVisible();

    // Cancel closes the wizard.
    await page.getByRole('button', { name: /^cancel$/i }).click();
    await expect(page.getByTestId('mcp-name')).toBeHidden();
  });
});
