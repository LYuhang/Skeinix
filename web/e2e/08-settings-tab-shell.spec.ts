/**
 * E2E: Settings tab shell + topbar avatar dropdown.
 *
 * Spec `2026-05-25-settings-tab-shell-design.md` §5. Six gates G1-G6.
 * Reuses the project's standard `seedAuthAndLocale` token fixture so
 * the auth-bootstrap path is identical to the rest of the e2e suite.
 *
 * G6 (zh locale) drives the second `test.describe.serial` block since
 * it depends on a different `locale` seed.
 */
import { test, expect } from '@playwright/test';
import {
  registerE2EUserToken,
  seedAuthAndLocale,
  seedTokenAndLocale,
} from './fixtures';

test.describe('Settings tab shell + user menu — en locale', () => {
  test.beforeEach(async ({ context }) => {
    await seedAuthAndLocale(context, 'en');
  });

  test('G1: topbar renders the avatar circle (not raw email)', async ({
    page,
  }) => {
    await page.goto('/workspace');
    const avatar = page.getByRole('button', { name: /open user menu/i });
    await expect(avatar).toBeVisible();
    // Avatar shows the first letter of the email, not the full email
    await expect(avatar).toHaveText(/^[A-Z]$/);
  });

  test('G2: avatar click opens dropdown with email + Settings + Sign out', async ({
    page,
  }) => {
    await page.goto('/workspace');
    await page
      .getByRole('button', { name: /open user menu/i })
      .click();
    // Email is shown in the dropdown header (any non-empty value the
    // dev-token resolves to — we don't pin the exact address)
    await expect(
      page.getByText(/[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}/i),
    ).toBeVisible();
    await expect(
      page.getByRole('menuitem', { name: /settings/i }),
    ).toBeVisible();
    await expect(
      page.getByRole('menuitem', { name: /sign out/i }),
    ).toBeVisible();
  });

  test('G3: Settings menuitem navigates to /settings with Preferences active', async ({
    page,
  }) => {
    await page.goto('/workspace');
    await page
      .getByRole('button', { name: /open user menu/i })
      .click();
    await page.getByRole('menuitem', { name: /settings/i }).click();
    await page.waitForURL(/\/settings(\?|$)/);
    // The default tab uses the canonical, query-free URL.
    expect(page.url()).toMatch(/\/settings$/);
    // Preferences tab is active
    const tab = page.getByRole('tab', { name: /preferences/i });
    await expect(tab).toHaveAttribute('data-state', 'active');
    // Language + Theme controls visible inside
    await expect(page.getByRole('button', { name: /中文/i })).toBeVisible();
    await expect(
      page.getByRole('button', { name: /english/i }),
    ).toBeVisible();
    await expect(
      page
        .getByRole('tabpanel', { name: /preferences/i })
        .getByRole('button', { name: /toggle theme/i }),
    ).toBeVisible();
  });

  test('G4: direct-link /settings?tab=preferences lands on Preferences', async ({
    page,
  }) => {
    await page.goto('/settings?tab=preferences');
    const tab = page.getByRole('tab', { name: /preferences/i });
    await expect(tab).toHaveAttribute('data-state', 'active');
  });

});

test.describe('Settings tab shell + user menu — zh locale', () => {
  test.beforeEach(async ({ context }) => {
    await seedAuthAndLocale(context, 'zh');
  });

  test('G6: i18n keys render in Chinese', async ({ page }) => {
    await page.goto('/settings?tab=preferences');
    // Tab label is 偏好 in zh
    await expect(page.getByRole('tab', { name: '偏好' })).toBeVisible();
    // Avatar dropdown shows 设置 / 登出 in Chinese
    await page
      .getByRole('button', { name: /打开用户菜单|open user menu/i })
      .click();
    await expect(page.getByRole('menuitem', { name: '设置' })).toBeVisible();
    await expect(
      page.getByRole('menuitem', { name: /登出|sign out/i }),
    ).toBeVisible();
  });
});

// Keep token-revoking logout last: the other cases in this file intentionally
// share one fixture identity so their API and UI tenant remain identical.
test.describe('Settings user menu — session termination', () => {
  test.beforeEach(async ({ context }) => {
    const token = await registerE2EUserToken();
    await seedTokenAndLocale(context, token, 'en');
  });

  test('G5: Sign out from dropdown logs the user out', async ({ page }) => {
    await page.goto('/workspace');
    await page
      .getByRole('button', { name: /open user menu/i })
      .click();
    await page.getByRole('menuitem', { name: /sign out/i }).click();
    await page.waitForURL(/\/login(\?|$)/);
    await page.goto('/workspace');
    await page.waitForURL(/\/login(\?|$)/);
  });
});
