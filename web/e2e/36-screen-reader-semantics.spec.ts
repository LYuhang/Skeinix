import { expect, test } from '@playwright/test';

import {
  accessibilityProperty,
  findAccessibilityNode,
  readAccessibilityTree,
} from './accessibility-tree';
import {
  createWorkflow,
  deleteWorkflow,
  seedAuthAndLocale,
  seedStartNode,
} from './fixtures';

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context, 'en');
});

test('signed-out Login exposes named controls in the computed accessibility tree', async ({
  context,
  page,
}) => {
  await context.clearCookies();
  await page.goto('/login');
  await expect(page.getByRole('heading', { level: 1, name: 'Sign in' })).toBeVisible();

  const nodes = await readAccessibilityTree(page);
  expect(findAccessibilityNode(nodes, 'main')).toBeDefined();
  expect(findAccessibilityNode(nodes, 'heading', 'Sign in')).toBeDefined();
  expect(findAccessibilityNode(nodes, 'textbox', 'Email')).toBeDefined();
  expect(findAccessibilityNode(nodes, 'textbox', 'Password')).toBeDefined();
  expect(findAccessibilityNode(nodes, 'button', 'Show password')).toBeDefined();
  expect(findAccessibilityNode(nodes, 'button', 'Sign in')).toBeDefined();

  const email = page.getByRole('textbox', { name: 'Email' });
  const password = page.getByRole('textbox', { name: 'Password' });
  const showPassword = page.getByRole('button', { name: 'Show password' });
  await expect(email).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Forgot password?' })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(password).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(showPassword).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Hide password' })).toBeFocused();
  await expect(password).toHaveAttribute('type', 'text');
});

test('Chat exposes a named conversation log, live status, and composer', async ({ page }) => {
  await page.goto('/chat');
  await expect(page.getByRole('log', { name: 'Conversation' })).toBeVisible({ timeout: 20_000 });
  await expect(page.getByRole('textbox', { name: 'Message the agent' })).toBeVisible();

  const nodes = await readAccessibilityTree(page);
  const conversation = findAccessibilityNode(nodes, 'log', 'Conversation');
  expect(conversation).toBeDefined();
  expect(accessibilityProperty(conversation, 'live')).toBe('polite');
  expect(findAccessibilityNode(nodes, 'textbox', 'Message the agent')).toBeDefined();
  expect(findAccessibilityNode(nodes, 'button', 'New Chat')).toBeDefined();
});

test('Workflow exposes its canvas and restores focus after settings closes', async ({ page }) => {
  const wfId = await createWorkflow(`e2e-screen-reader-${Date.now()}`);
  await seedStartNode(wfId);
  try {
    await page.goto(`/workflow/${wfId}`);
    const settingsButton = page.locator('[data-action="canvas-settings"]');
    await expect(settingsButton).toBeVisible({ timeout: 20_000 });

    let nodes = await readAccessibilityTree(page);
    expect(findAccessibilityNode(nodes, 'region', 'Workflow canvas')).toBeDefined();
    expect(findAccessibilityNode(nodes, 'button', 'Workflow settings')).toBeDefined();

    await settingsButton.focus();
    await page.keyboard.press('Enter');
    const dialog = page.getByRole('dialog', { name: 'Workflow settings' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('tab', { name: 'Timeouts' })).toBeFocused();

    nodes = await readAccessibilityTree(page);
    expect(findAccessibilityNode(nodes, 'dialog', 'Workflow settings')).toBeDefined();
    expect(findAccessibilityNode(nodes, 'tablist')).toBeDefined();
    expect(findAccessibilityNode(nodes, 'tab', 'Timeouts')).toBeDefined();
    expect(findAccessibilityNode(nodes, 'tab', 'Python')).toBeDefined();
    expect(findAccessibilityNode(nodes, 'tab', 'Network')).toBeDefined();

    await page.keyboard.press('Escape');
    await expect(dialog).toBeHidden();
    await expect(settingsButton).toBeFocused();
  } finally {
    await deleteWorkflow(wfId);
  }
});
