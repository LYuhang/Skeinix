/**
 * Authentication pages and route-guard end-to-end tests.
 *
 * Three scenarios:
 *   1. Register → Chat. A fresh identity registers via the UI and the user
 *      lands at the product's default `/chat` workbench.
 *   2. Logout → login. With the user signed in from (1)'s setup we click
 *      the topbar "登出" button and end up at `/login`.
 *   3. Auth gate. Without a token, visiting `/workspace` directly bounces
 *      to `/login`.
 *
 * We never reuse a development token across these tests: the backend
 * accepts real password credentials, and the dev-token path is being
 * retired. Each test that needs an authenticated state registers a fresh
 * random-email user — the auth tables aren't truncated between runs so
 * randomness is mandatory for idempotency.
 */
import { test, expect, type Page } from '@playwright/test';

function uniqueEmail(): string {
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  return `e2e-auth-${suffix}@example.com`;
}

async function clearAuthStorage(page: Page): Promise<void> {
  // Run BEFORE any navigation so the SPA boots without a stored token.
  await page.addInitScript(() => {
    try {
      window.localStorage.removeItem('vibecanvas.token');
    } catch {
      /* ignore */
    }
  });
}

test.describe('authentication', () => {
  test('register flow lands on /chat', async ({ page }) => {
    await clearAuthStorage(page);
    const email = uniqueEmail();

    await page.goto('/signup');
    await expect(page).toHaveURL(/\/signup$/);

    await page.locator('#signup-username').fill(`e2e_${Date.now()}`);
    await page.locator('#signup-email').fill(email);
    // Two password fields (password + confirm). Fill both by id to avoid
    // the ambiguity of `getByLabel(/password/i)`.
    await page.locator('#signup-password').fill('s3cretp@ss');
    await page.locator('#signup-confirm').fill('s3cretp@ss');

    await page.getByRole('button', { name: /create account|创建账户/i }).click();

    await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  });

  test('logout returns the user to /login', async ({ page }) => {
    await clearAuthStorage(page);
    const email = uniqueEmail();

    // Sign up first so we have a session to log out from.
    await page.goto('/signup');
    await page.locator('#signup-username').fill(`e2e_${Date.now()}`);
    await page.locator('#signup-email').fill(email);
    await page.locator('#signup-password').fill('s3cretp@ss');
    await page.locator('#signup-confirm').fill('s3cretp@ss');
    await page.getByRole('button', { name: /create account|创建账户/i }).click();
    await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });

    // Logout lives inside the topbar user-menu dropdown (settings-shell T2).
    // Open the avatar dropdown first, then click the Sign out menuitem.
    // `data-action` selectors dodge i18n on both elements.
    await page.locator('[data-action="open-user-menu"]').click();
    await page.locator('[data-action="logout"]').click();
    await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 });
  });

  test('unauthenticated /workspace redirects to /login', async ({ page }) => {
    await clearAuthStorage(page);
    await page.goto('/workspace');
    await expect(page).toHaveURL(/\/login$/, { timeout: 5_000 });
  });
});
