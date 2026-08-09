import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import {
  e2eSessionHeaders,
  registerE2EUserToken,
  seedTokenAndLocale,
} from './fixtures';

const API_BASE = process.env.VIBECANVAS_API_BASE ?? 'http://127.0.0.1:8000';
test.setTimeout(420_000);

let token = '';

async function api(path: string, init: RequestInit = {}) {
  const headers = new Headers(e2eSessionHeaders(token));
  new Headers(init.headers).forEach((value, key) => headers.set(key, value));
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!response.ok) {
    throw new Error(`${init.method ?? 'GET'} ${path} failed: ${response.status} ${await response.text()}`);
  }
  return response;
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

async function sendAndWait(page: Page, prompt: string, expected: RegExp) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  await expect(composer).toBeEnabled({ timeout: 20_000 });
  await composer.fill(prompt);
  const started = Date.now();
  await page.locator('[data-action="agent-composer-send"]').click();
  await expect(page.locator('[data-action="agent-composer-stop"]')).toBeVisible({
    timeout: 20_000,
  });
  await expect(composer).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');

  const answer = page.locator('[data-message-role="assistant"]').filter({
    hasText: expected,
  }).last();
  await expect(answer).toBeVisible({ timeout: 180_000 });
  const firstVisibleMs = Date.now() - started;
  await expect(page.locator('[data-role="agent-thinking"]')).toHaveCount(0);
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible({
    timeout: 180_000,
  });
  await expect(page.locator('[data-role="agent-stream-announcement"]')).toHaveText(
    'Agent response complete',
  );
  return firstVisibleMs;
}

test('keeps transcript incremental, hides Thinking on output, reuses Runtime, and searches reliably', async ({
  page,
}: {
  page: Page;
}) => {
  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
    timeout: 20_000,
  });
  await page.locator('[data-action="chat-new"]').click();

  const firstMs = await sendAndWait(
    page,
    'Reply with exactly this token and no other text: FIRST_READY',
    /FIRST_READY/,
  );
  const firstAnswer = page.locator('[data-message-role="assistant"]').filter({
    hasText: /FIRST_READY/,
  }).last();
  await expect(firstAnswer).toBeVisible();

  const composer = page.locator('[data-role="agent-composer-input"]');
  await composer.fill('Reply with exactly this token and no other text: SECOND_READY');
  const secondStarted = Date.now();
  await page.locator('[data-action="agent-composer-send"]').click();
  // An optimistic user append must never clear already-rendered history.
  await expect(firstAnswer).toBeVisible();
  const secondAnswer = page.locator('[data-message-role="assistant"]').filter({
    hasText: /SECOND_READY/,
  }).last();
  await expect(secondAnswer).toBeVisible({ timeout: 180_000 });
  const secondMs = Date.now() - secondStarted;
  await expect(page.locator('[data-role="agent-thinking"]')).toHaveCount(0);
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible({
    timeout: 180_000,
  });
  await expect(page.locator('[data-role="agent-stream-announcement"]')).toHaveText(
    'Agent response complete',
  );

  const searchMs = await sendAndWait(
    page,
    [
      'Call web_search exactly once with query "2026 Agent framework latest progress".',
      'After the tool returns, reply with the token SEARCH_READY and briefly summarize one result.',
    ].join(' '),
    /SEARCH_READY/,
  );
  await expect(page.getByText(/UnexpectedMessage|fatal alert/i)).toHaveCount(0);

  console.log(JSON.stringify({
    first_visible_ms: firstMs,
    second_visible_ms: secondMs,
    search_visible_ms: searchMs,
  }));
  await page.screenshot({
    path: '/tmp/vibecanvas-chat-acceptance.png',
    fullPage: true,
  });
});
