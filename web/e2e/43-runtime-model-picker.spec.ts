import { expect, test } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';
import {
  provisionRealRuntime,
  type RealRuntimeProfile,
} from './real-runtime-profile';

test.describe('Runtime-compatible model picker', () => {
  const session = new E2ECookieSession();
  let profile: RealRuntimeProfile;

  test.beforeAll(async () => {
    await session.register('runtime-model-picker');
    profile = await provisionRealRuntime(session, 'codex');
  });

  test.afterAll(() => {
    profile?.cleanup();
  });

  test.beforeEach(async ({ context }) => {
    await session.seed(context, 'en');
  });

  test('uses a source-first catalog from the live Runtime capabilities', async ({ page }) => {
    await page.goto('/chat', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
      timeout: 30_000,
    });

    const picker = page.locator('[data-role="chat-model-select"]');
    await expect(picker).toBeEnabled({ timeout: 30_000 });
    await picker.click();
    await expect(page.getByRole('heading', { name: 'Choose a model source' })).toBeVisible();

    const source = page.locator(
      `[data-role="chat-model-source-option"][data-model-source="${profile.modelSourceId}"]`,
    );
    await expect(source).toBeVisible();
    await source.click();

    const model = page.locator(
      `[data-role="chat-model-option"][data-model-id="${profile.modelId}"]`,
    );
    await expect(model).toBeVisible();
    await model.click();
    await expect(picker).toBeEnabled();

    // Runtime is a Settings-level choice; only model and reasoning belong in
    // the per-Turn Chat controls.
    await expect(page.getByRole('combobox', { name: /runtime/i })).toHaveCount(0);
  });
});
