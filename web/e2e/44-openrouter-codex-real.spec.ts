import fs from 'node:fs';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

const API_BASE = process.env.VIBECANVAS_API_BASE ?? 'http://localhost:8000';
const APP_ORIGIN = process.env.VIBECANVAS_E2E_ORIGIN
  ?? `http://${process.env.VIBECANVAS_E2E_HOST ?? 'localhost'}:${process.env.VIBECANVAS_WEB_PORT ?? '9001'}`;
const SESSION_FILE = process.env.SKEINIX_E2E_EXISTING_SESSION_FILE;
const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/[^/]+\/chats\/([^/]+)\/messages$/;

interface ExistingSession {
  session: string;
  csrf: string;
  session_id: string;
  user_id: string;
  organization_id: string;
}

function sessionMaterial(): ExistingSession {
  if (!SESSION_FILE) throw new Error('SKEINIX_E2E_EXISTING_SESSION_FILE is required');
  const material = JSON.parse(fs.readFileSync(SESSION_FILE, 'utf8')) as ExistingSession;
  for (const field of ['session', 'csrf', 'session_id', 'user_id', 'organization_id'] as const) {
    if (!material[field]) throw new Error(`existing Session is missing ${field}`);
  }
  return material;
}

async function api(
  session: ExistingSession,
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      Cookie: `vibecanvas-web-session=${session.session}; vibecanvas-web-csrf=${session.csrf}`,
      Origin: APP_ORIGIN,
      ...(method === 'GET' || method === 'HEAD'
        ? {}
        : { 'X-CSRF-Token': session.csrf }),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw new Error(`${method} ${path} failed: ${response.status} ${await response.text()}`);
  }
  return response;
}

async function seed(context: BrowserContext, session: ExistingSession) {
  await context.addCookies([
    { name: 'vibecanvas-web-session', value: session.session, url: APP_ORIGIN },
    { name: 'vibecanvas-web-csrf', value: session.csrf, url: APP_ORIGIN },
  ]);
  await context.addInitScript(() => {
    window.localStorage.setItem('vibecanvas.locale', 'en');
  });
}

async function selectOxAlpha(page: Page) {
  const picker = page.locator('[data-role="chat-model-select"]');
  await expect(picker).toBeEnabled({ timeout: 30_000 });
  await picker.click();
  await page.locator(
    '[data-role="chat-model-source-option"][data-model-source="openrouter_oauth"]',
  ).click();
  const search = page.getByPlaceholder(/Search models, providers, or free/i);
  await search.fill('stealth/ox-alpha');
  const option = page.locator('[data-role="chat-model-option"]').filter({
    hasText: 'stealth/ox-alpha',
  });
  await expect(option).toHaveCount(1);
  await option.click();

  await page.locator('[data-role="chat-composer-options-toggle"]').click();
  const effort = page.locator('[data-role="chat-reasoning-effort-select"]');
  await expect(effort).toBeEnabled();
  await effort.click();
  await page.getByRole('option', { name: 'Maximum' }).click();
}

async function runTurn(
  page: Page,
  session: ExistingSession,
  prompt: string,
) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  await composer.fill(prompt);
  const [messageResponse] = await Promise.all([
    page.waitForResponse((response) => (
      response.request().method() === 'POST'
      && MESSAGE_PATH.test(new URL(response.url()).pathname)
    ), { timeout: 30_000 }),
    page.locator('[data-action="agent-composer-send"]').click(),
  ]);
  expect(messageResponse.ok()).toBe(true);
  const match = new URL(messageResponse.url()).pathname.match(MESSAGE_PATH);
  const chatId = match?.[1];
  const turnId = messageResponse.headers()['x-turn-id'];
  expect(chatId).toBeTruthy();
  expect(turnId).toMatch(/^t_/);
  const replay = await api(
    session,
    `/api/v1/chats/${encodeURIComponent(chatId!)}/turns/${encodeURIComponent(turnId!)}/stream`,
  );
  const stream = await replay.text();
  const error = [...stream.matchAll(/^event: error\ndata: (.+)$/gm)].at(-1)?.[1];
  if (error) throw new Error(`OpenRouter Codex Turn failed: ${error}`);
  await expect(page.locator('[data-role="agent-thinking"]')).toHaveCount(0, {
    timeout: 180_000,
  });
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible();
  return { chatId: chatId!, turnId: turnId!, stream };
}

test.describe('OpenRouter OAuth model through Codex', () => {
  test.skip(!SESSION_FILE, 'requires an explicitly authorized existing account Session');
  test.setTimeout(600_000);
  const session = SESSION_FILE ? sessionMaterial() : null;

  test.beforeAll(async () => {
    if (!session) return;
    await api(session, '/api/v1/agent-runtime/settings', {
      method: 'PUT',
      body: JSON.stringify({ default_runtime_type: 'codex' }),
    });
  });

  test.beforeEach(async ({ context }) => {
    if (session) await seed(context, session);
  });

  test('completes text, tool, reload, and binding audit with stealth/ox-alpha', async ({ page }) => {
    if (!session) return;
    await page.goto('/chat', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
      timeout: 30_000,
    });
    await page.locator('[data-action="chat-new"]').click();
    await selectOxAlpha(page);

    const marker = `OPENROUTER_OX_${Date.now()}`;
    const first = await runTurn(
      page,
      session,
      `Reply with exactly this token and no other text: ${marker}`,
    );
    await expect(page.locator('[data-message-role="assistant"]').last()).toContainText(marker);

    const second = await runTurn(
      page,
      session,
      'Use a local filesystem tool to create hello.txt containing exactly '
        + 'OPENROUTER_TOOL_OK, then read it back and report the exact content.',
    );
    expect(second.chatId).toBe(first.chatId);
    await page.getByRole('button', { name: /tools used/i }).last().click();
    await expect(page.locator('[data-role="tool-call"]').last()).toHaveAttribute(
      'data-tool-status',
      'done',
      { timeout: 60_000 },
    );
    await expect(page.locator('[data-message-role="assistant"]').last())
      .toContainText('OPENROUTER_TOOL_OK');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.locator(`button[data-chat-id="${first.chatId}"]`).click();
    await expect(page.locator('[data-role="chat-model-select"]')).toContainText('Ox Alpha');
    await page.locator('[data-role="chat-composer-options-toggle"]').click();
    await expect(page.locator('[data-role="chat-reasoning-effort-select"]'))
      .toContainText('Maximum');
    await expect(page.locator('[data-message-role="assistant"]').last())
      .toContainText('OPENROUTER_TOOL_OK');
  });
});
