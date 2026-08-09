/**
 * E2E: seeded-token login + chat landing.
 *
 * Canonical T21 spec — exercises the auth bootstrap path and confirms the
 * `/chat` route renders. We seed a real test-user token into storage before
 * the page loads so the auth bootstrap stays deterministic.
 *
 * The companion specs (02–06) are listed in `e2e/README.md`; they land
 * in T22 along with the seed-data harness.
 */
import { test, expect } from '@playwright/test';
import { seedAuthAndLocale } from './fixtures';

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context);
});

test('logs in with a seeded token and lands on /chat', async ({ page }) => {
  await page.goto('/');
  // The application shell owns the root redirect and currently opens Chat.
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible();
  await expect(page.getByRole('button', { name: 'New Chat', exact: true })).toBeVisible();
});
