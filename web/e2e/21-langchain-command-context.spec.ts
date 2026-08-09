import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import {
  e2eSessionHeaders,
  registerE2EUserToken,
  seedTokenAndLocale,
} from './fixtures';

const API_BASE = process.env.VIBECANVAS_API_BASE ?? 'http://127.0.0.1:8000';
const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/([^/]+)\/chats\/([^/]+)\/messages$/;

test.setTimeout(420_000);

let token = '';
const workflowNames = [
  `Command Context First ${Date.now()}`,
  `Command Context Second ${Date.now()}`,
];

async function api(path: string, init: RequestInit = {}, allowError = false) {
  const headers = new Headers(e2eSessionHeaders(token));
  new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!allowError && !response.ok) {
    throw new Error(`${init.method ?? 'GET'} ${path} failed: ${response.status} ${await response.text()}`);
  }
  return response;
}

async function chooseAlwaysAllow(page: Page) {
  await page.locator('[data-role="chat-composer-options-toggle"]').click();
  await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
}

async function sendBuildTurn(page: Page, prompt: string, completion: string) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  await composer.fill(prompt);
  await Promise.all([
    page.waitForRequest((request) => (
      request.method() === 'POST'
      && MESSAGE_PATH.test(new URL(request.url()).pathname)
    ), { timeout: 30_000 }),
    page.locator('[data-action="agent-composer-send"]').click(),
  ]);
  await expect(
    page.locator('[data-message-role="assistant"]').filter({ hasText: completion }).last(),
  ).toBeVisible({ timeout: 240_000 });
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible({
    timeout: 60_000,
  });
}

test.beforeAll(async () => {
  token = await registerE2EUserToken();
  await api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: 'langchain' }),
  });
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await seedTokenAndLocale(context, token, 'en');
});

test.afterAll(async () => {
  const response = await api('/api/v1/workflows', {}, true);
  if (!response.ok) return;
  const payload = await response.json() as unknown;
  const rows = Array.isArray(payload)
    ? payload
    : (
      payload
      && typeof payload === 'object'
      && Array.isArray((payload as { items?: unknown[] }).items)
        ? (payload as { items: unknown[] }).items
        : []
    );
  for (const row of rows) {
    if (!row || typeof row !== 'object') continue;
    const workflow = row as { wf_id?: string; name?: string };
    if (!workflow.wf_id || !workflowNames.includes(workflow.name ?? '')) continue;
    await api(`/api/v1/workflows/${encodeURIComponent(workflow.wf_id)}`, {
      method: 'DELETE',
    }, true);
  }
});

test('repeated /build Turns keep product history and expose the same Platform MCP', async ({
  page,
}: {
  page: Page;
}) => {
  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
    timeout: 20_000,
  });
  await page.locator('[data-action="chat-new"]').click();
  await chooseAlwaysAllow(page);

  const first = [
    '/build',
    'Call the create_workflow tool exactly once with',
    `name "${workflowNames[0]}".`,
    'After the tool succeeds, reply BUILD_FIRST_DONE.',
  ].join(' ');
  await sendBuildTurn(page, first, 'BUILD_FIRST_DONE');

  const second = [
    '/build',
    'Call the create_workflow tool exactly once with',
    `name "${workflowNames[1]}".`,
    'After the tool succeeds, reply BUILD_SECOND_DONE.',
  ].join(' ');
  await sendBuildTurn(page, second, 'BUILD_SECOND_DONE');

  await expect(page.locator('[data-message-role="user"]').filter({ hasText: first })).toHaveCount(1);
  await expect(page.locator('[data-message-role="user"]').filter({ hasText: second })).toHaveCount(1);
  await expect(page.getByRole('button', {
    name: /1 tool used.*create_workflow/i,
  })).toHaveCount(2);

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-message-role="user"]').filter({ hasText: first })).toHaveCount(1);
  await expect(page.locator('[data-message-role="user"]').filter({ hasText: second })).toHaveCount(1);
});
