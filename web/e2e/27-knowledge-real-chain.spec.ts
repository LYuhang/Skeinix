import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

const session = new E2ECookieSession();
const unique = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const kbName = `Knowledge acceptance ${unique}`;
const identifier = `VIBECANVAS_KNOWLEDGE_E2E_${unique}`;

test.setTimeout(600_000);

test.beforeAll(async () => {
  await session.register('knowledge-real-e2e');
  await session.api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: 'langchain' }),
  });
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'en');
});

async function chooseAlwaysAllow(page: Page) {
  const options = page.locator('[data-role="chat-composer-options-toggle"]');
  await expect(options).toBeVisible({ timeout: 20_000 });
  await options.click();
  await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
}

test('upload, index, retrieve, and use Knowledge through a real Chat turn', async ({ page }) => {
  const browserErrors: string[] = [];
  page.on('pageerror', (error) => browserErrors.push(error.message));
  page.on('requestfailed', (request) => {
    const errorText = request.failure()?.errorText ?? '';
    // A document navigation intentionally cancels superseded React Query GETs
    // and an already-rendered retrieval request. These are browser lifecycle
    // aborts, not transport failures.
    if (!errorText.includes('ERR_ABORTED')) {
      browserErrors.push(`${request.method()} ${request.url()}: ${errorText}`);
    }
  });

  await page.goto('/knowledge');
  await expect(page.getByRole('heading', { name: 'Knowledge', exact: true })).toBeVisible({ timeout: 30_000 });
  await page.getByRole('button', { name: 'New knowledge base' }).first().click();
  await page.getByLabel('Name').fill(kbName);
  await page.getByLabel('Description').fill('Real browser acceptance knowledge collection');
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  const row = page.getByRole('link', { name: new RegExp(kbName) });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await row.click();

  await expect(page.getByRole('heading', { name: kbName })).toBeVisible({ timeout: 30_000 });
  await page.locator('input[type="file"]').setInputFiles({
    name: 'acceptance.md',
    mimeType: 'text/markdown',
    buffer: Buffer.from([
      '# Acceptance fact',
      '',
      `The exact verification identifier is ${identifier}.`,
      'Return this identifier verbatim when it is requested.',
    ].join('\n')),
  });

  await page.getByRole('tab', { name: /Sources/ }).click();
  await expect(page.getByRole('tree', { name: 'Source files' })).toBeVisible();
  await expect(page.getByRole('treeitem', { name: /acceptance\.md/i })).toBeVisible();
  await expect(page.getByText('indexed', { exact: true }).first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText('Agent discovery summary')).toHaveCount(0);
  await page.getByRole('treeitem', { name: /acceptance\.md/i }).click();
  await expect(page.getByText(identifier, { exact: false }).last()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('tab', { name: 'Retrieval' })).toHaveCount(0);
  await page.screenshot({
    path: '/tmp/vibecanvas-knowledge-sources-explorer.png',
    fullPage: true,
  });

  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-action="chat-new"]').click();
  await chooseAlwaysAllow(page);
  const prompt = [
    '/knowledge',
    'First call list_knowledge_bases. Find the knowledge base named',
    `"${kbName}". Treat it as a virtual folder: call list_knowledge_files,`,
    `then search_knowledge with the exact query "${identifier}", and finally`,
    'call read_knowledge_file for acceptance.md before answering.',
    `After those tools succeed, reply exactly KNOWLEDGE_CHAT_OK ${identifier}`,
  ].join(' ');
  const composer = page.locator('[data-role="agent-composer-input"]');
  await composer.fill(prompt);
  await page.locator('[data-action="agent-composer-send"]').click();
  await expect(
    page.locator('[data-message-role="assistant"]').filter({
      hasText: `KNOWLEDGE_CHAT_OK ${identifier}`,
    }).last(),
  ).toBeVisible({ timeout: 300_000 });
  await expect(page.getByText(/list_knowledge_bases/).last()).toBeVisible();
  await expect(page.getByText(/list_knowledge_files/).last()).toBeVisible();
  await expect(page.getByText(/search_knowledge/).last()).toBeVisible();
  await expect(page.getByText(/read_knowledge_file/).last()).toBeVisible();
  await page.screenshot({
    path: '/tmp/vibecanvas-knowledge-chat-agentic.png',
    fullPage: true,
  });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-message-role="user"]').filter({ hasText: prompt })).toHaveCount(1);
  expect(browserErrors).toEqual([]);
});
