import { expect, test } from '@playwright/test';

import { findAccessibilityNode, readAccessibilityTree } from './accessibility-tree';

function uniqueSuffix(): string {
  return `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
}

test('Cookie Session preserves organization switching and workflow creation', async ({
  context,
  page,
}) => {
  test.setTimeout(90_000);
  const suffix = uniqueSuffix();
  const email = `cookie-org-${suffix}@example.com`;
  const password = 'Browser-secure-42!';
  const organizationName = `Browser Org ${suffix}`;
  const organizationSlug = `browser-${suffix}`;
  const workflowName = `URL capable workflow ${suffix}`;

  await page.goto('/signup');
  await page.locator('#signup-username').fill(`browser_${suffix}`);
  await page.locator('#signup-email').fill(email);
  await page.locator('#signup-password').fill(password);
  await page.locator('#signup-confirm').fill(password);
  await page.getByRole('button', { name: /create account|创建账户/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 20_000 });

  const cookies = await context.cookies();
  const sessionCookie = cookies.find(
    (cookie) => cookie.name.endsWith('vibecanvas-web-session'),
  );
  const csrfCookie = cookies.find(
    (cookie) => cookie.name.endsWith('vibecanvas-web-csrf'),
  );
  expect(sessionCookie?.httpOnly).toBe(true);
  expect(csrfCookie?.httpOnly).toBe(false);
  expect(
    await page.evaluate(() => window.localStorage.getItem('vibecanvas.token')),
  ).toBeNull();
  expect(
    await page.evaluate(() => Object.keys(window.localStorage).filter(
      (key) => /token|session|auth/i.test(key),
    )),
  ).toEqual([]);

  await page.goto('/settings?tab=organization');
  await expect(page.locator('[data-testid="settings-tab-organization"]')).toHaveCount(0);

  const created = await page.evaluate(async ({ name, slug }) => {
    const csrf = document.cookie
      .split('; ')
      .find((part) => part.split('=', 1)[0]?.endsWith('vibecanvas-web-csrf'))
      ?.split('=').slice(1).join('=');
    const response = await fetch('/api/v1/organizations', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(csrf ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}),
      },
      body: JSON.stringify({ name, slug }),
    });
    return { ok: response.ok, status: response.status, body: await response.text() };
  }, { name: organizationName, slug: organizationSlug });
  expect(created, created.body).toMatchObject({ ok: true, status: 201 });

  await page.goto('/chat');
  await page.locator('[data-testid="organization-switcher"]').click();
  await page.getByRole('menuitem').filter({ hasText: organizationName }).click();
  await expect(page.locator('[data-testid="organization-switcher"]')).toContainText(
    organizationName,
    { timeout: 20_000 },
  );

  await page.goto('/settings?tab=organization');
  await page.locator('[data-testid="settings-tab-organization"]').click();
  await expect(page.getByRole('tablist', {
    name: /Organization sections|组织设置分区/,
  })).toBeVisible();
  expect(
    findAccessibilityNode(
      await readAccessibilityTree(page),
      'tablist',
      /Organization sections|组织设置分区/,
    ),
  ).toBeDefined();

  await page.goto('/workspace');
  await page.getByRole('button', { name: /new workflow|新建工作流/i }).first().click();
  await page.locator('[data-testid="create-workflow-name"]').fill(workflowName);
  await page.locator('#new-workflow-description').fill(
    'HTTPS URL inputs remain supported and safely fetched.',
  );
  await page.getByRole('dialog').getByRole('button', {
    name: /^create$|^创建$/i,
  }).click();
  await expect(page).toHaveURL(/\/workflow\/[^/]+$/, { timeout: 20_000 });

  await page.locator('[data-testid="organization-switcher"]').click();
  const personalWorkspace = page.getByRole('menuitem').filter({
    hasText: /personal|个人/i,
  });
  await expect(personalWorkspace).toHaveCount(1);
  await personalWorkspace.click();
  const restoredSwitcher = page.locator('[data-testid="organization-switcher"]');
  await expect(restoredSwitcher).toBeVisible({ timeout: 20_000 });
  await expect(restoredSwitcher).not.toContainText(
    organizationName,
    { timeout: 15_000 },
  );

  await page.locator('[data-action="open-user-menu"]').click();
  await page.locator('[data-action="logout"]').click();
  await expect(page).toHaveURL(/\/login$/, { timeout: 10_000 });
  expect(
    (await context.cookies()).some(
      (cookie) => cookie.name.endsWith('vibecanvas-web-session'),
    ),
  ).toBe(false);

  await page.locator('#login-email').fill(email);
  await page.locator('#login-password').fill(password);
  await page.getByRole('button', { name: /sign in|登录/i }).click();
  await expect(page).toHaveURL(/\/chat$/, { timeout: 15_000 });
  expect(
    (await context.cookies()).some(
      (cookie) => cookie.name.endsWith('vibecanvas-web-session') && cookie.httpOnly,
    ),
  ).toBe(true);
});
