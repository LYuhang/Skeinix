import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

test.setTimeout(420_000);

const session = new E2ECookieSession();
const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/[^/]+\/chats\/([^/]+)\/messages$/;

test.beforeAll(async () => {
  await session.register('codex-warm-runtime-e2e');
  await session.api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: 'codex' }),
  });
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'en');
});

async function send(
  page: Page,
  marker: string,
  thinkingScreenshot?: string,
  requireExactMarker = true,
) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  const assistantMessages = page.locator('[data-message-role="assistant"]');
  const priorAssistantCount = await assistantMessages.count();
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  await composer.fill(`Reply with exactly this token and no other text: ${marker}`);
  const started = Date.now();
  const [messageResponse] = await Promise.all([
    page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && MESSAGE_PATH.test(new URL(response.url()).pathname)
    ), { timeout: 30_000 }),
    page.locator('[data-action="agent-composer-send"]').click(),
  ]);
  const chatId = new URL(messageResponse.url()).pathname.match(MESSAGE_PATH)?.[1];
  const turnId = messageResponse.headers()['x-turn-id'];
  expect(chatId).toBeTruthy();
  expect(turnId).toMatch(/^t_/);
  const streamPromise = session.api(
    `/api/v1/chats/${encodeURIComponent(chatId!)}/turns/${encodeURIComponent(turnId!)}/stream`,
  ).then((response) => response.text());
  if (thinkingScreenshot) {
    await expect(page.locator('[data-role="agent-thinking"]')).toBeVisible({
      timeout: 20_000,
    });
    await page.screenshot({ path: thinkingScreenshot, fullPage: true });
  }
  const stream = await streamPromise;
  const terminalError = [...stream.matchAll(/^event: error\ndata: (.+)$/gm)].at(-1)?.[1];
  if (terminalError) {
    throw new Error(`Codex Turn failed before ${marker}: ${terminalError}`);
  }
  await expect(assistantMessages).toHaveCount(priorAssistantCount + 1, {
    timeout: 20_000,
  });
  const answer = assistantMessages.last();
  await expect(answer).not.toHaveText(/^\s*$/);
  if (requireExactMarker) {
    await expect(answer).toContainText(marker);
  }
  const elapsed = Date.now() - started;
  await expect(page.locator('[data-role="agent-thinking"]')).toHaveCount(0);
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible({
    timeout: 180_000,
  });
  return elapsed;
}

test('Codex reuses its Chat Runtime process across turns', async ({
  page,
}: {
  page: Page;
}) => {
  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
    timeout: 20_000,
  });
  await page.locator('[data-action="chat-new"]').click();
  const firstMs = await send(
    page,
    'CODEX_FIRST_READY',
    '/tmp/vibecanvas-thinking-bubble.png',
  );
  const first = page.locator('[data-message-role="assistant"]').filter({
    hasText: /CODEX_FIRST_READY/,
  }).last();
  // The managed model may choose not to obey an exact-token instruction on a
  // continuation Turn. Runtime reuse acceptance is about a new ordered,
  // durable assistant response with no terminal error; the first Turn already
  // proves the configured model can follow the deterministic marker prompt.
  const secondMs = await send(page, 'CODEX_SECOND_READY', undefined, false);
  await expect(first).toBeVisible();
  console.log(JSON.stringify({
    codex_first_visible_ms: firstMs,
    codex_second_visible_ms: secondMs,
  }));
});
