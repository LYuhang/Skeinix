import { randomUUID } from 'node:crypto';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';
import {
  provisionRealRuntime,
  type RealRuntimeProfile,
} from './real-runtime-profile';

test.setTimeout(900_000);

const session = new E2ECookieSession();
const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/[^/]+\/chats\/([^/]+)\/messages$/;
type AuthMode = 'chatgpt_account' | 'managed_api';
type ModelOption = { id: string; label: string; provider?: string };

const modelOptions = new Map<AuthMode, ModelOption>();
let runtimeProfile: RealRuntimeProfile | null = null;

function workspaceScope(chatId: string) {
  return `__chatws_v2_${Buffer.from(chatId, 'utf8').toString('base64url')}`;
}

async function send(page: Page, prompt: string) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  const assistants = page.locator('[data-message-role="assistant"]')
    .filter({ has: page.locator('[data-role="markdown"]') });
  const before = await assistants.count();
  await expect(composer).toBeEnabled({ timeout: 30_000 });
  await composer.fill(prompt);
  const [response] = await Promise.all([
    page.waitForResponse((candidate) => (
      candidate.request().method() === 'POST'
      && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
    ), { timeout: 30_000 }),
    page.locator('[data-action="agent-composer-send"]').click(),
  ]);
  const chatId = new URL(response.url()).pathname.match(MESSAGE_PATH)?.[1];
  const turnId = response.headers()['x-turn-id'];
  expect(chatId).toBeTruthy();
  expect(turnId).toMatch(/^t_/);
  const stream = await session.api(
    `/api/v1/chats/${encodeURIComponent(chatId!)}/turns/${encodeURIComponent(turnId!)}/stream`,
  ).then((result) => result.text());
  const terminalError = [...stream.matchAll(/^event: error\ndata: (.+)$/gm)].at(-1)?.[1];
  if (terminalError) throw new Error(`Codex turn failed: ${terminalError}`);
  await expect.poll(() => assistants.count(), {
    timeout: 30_000,
    message: 'a new completed Codex answer',
  }).toBeGreaterThan(before);
  const answer = assistants.last();
  await expect(answer).not.toHaveText(/^\s*$/);
  await expect(page.locator('[data-role="agent-thinking"]')).toHaveCount(0);
  return { chatId: chatId!, turnId: turnId!, answer };
}

test.beforeAll(async () => {
  await session.register('codex-vfs-resume-e2e');
  runtimeProfile = await provisionRealRuntime(session, 'codex');

  const capabilities = await session.api('/api/v1/agent-runtime/capabilities')
    .then((response) => response.json()) as {
      authenticated: boolean | null;
      models: ModelOption[];
    };
  expect(capabilities.authenticated).toBe(true);
  const account = capabilities.models.find((model) => model.provider === 'chatgpt');
  const managed = capabilities.models.find((model) => (
    model.id === 'codex:default' || model.id.startsWith('codex:managed:')
  ));
  if (account) modelOptions.set('chatgpt_account', account);
  if (managed) modelOptions.set('managed_api', managed);
});

test.afterAll(() => {
  runtimeProfile?.cleanup();
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'zh');
});

for (const authMode of ['chatgpt_account', 'managed_api'] as const satisfies readonly AuthMode[]) {
test(`Codex ${authMode} restores one Chat from encrypted Runtime state after sandbox loss`, async ({
    page,
  }, testInfo) => {
  const selectedModel = modelOptions.get(authMode);
  test.skip(!selectedModel, `Codex ${authMode} is not configured in this environment`);
  if (!selectedModel) return;
  const marker = `CHAT_VFS_RESUME_${randomUUID().replaceAll('-', '').slice(0, 16)}`;
  await page.goto('/chat', { timeout: 30_000 });
  await page.waitForLoadState('networkidle', { timeout: 30_000 });
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.locator('[data-action="chat-new"]').click({ timeout: 30_000 });
  await expect(page.locator('[data-role="chat-model-select"]')).toBeEnabled({
    timeout: 30_000,
  });
  await page.locator('[data-role="chat-model-select"]').click({ timeout: 30_000 });
  await page.getByRole('option').filter({ hasText: selectedModel.label }).first().click({
    timeout: 30_000,
  });
  await expect(page.locator('[data-role="chat-model-select"]')).toContainText(
    selectedModel.label,
  );
  await page.locator('[data-role="chat-composer-options-toggle"]').click({ timeout: 30_000 });
  await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);

  const first = await send(page, [
    `Remember this private resume marker for this Chat: ${marker}.`,
    'Create /data/AGENTS.md containing exactly:',
    `When asked for the private resume marker, answer ${marker}.`,
    'Create /data/MEMORY.md containing exactly:',
    `This Chat remembers ${marker}.`,
    'Create /runtime/.codex/AGENTS.md containing exactly:',
    `Global Chat guidance: preserve and report ${marker}.`,
    'Create /runtime/home/MEMORY.md containing exactly:',
    `Codex home memory: ${marker}.`,
    `After actually writing all four files, reply with FIRST_SAVED ${marker}.`,
  ].join('\n'));
  await expect(first.answer).toContainText(marker);
  await page.screenshot({
    path: testInfo.outputPath(`${authMode}-codex-vfs-resume-first-turn.png`),
    fullPage: true,
  });

  const scopeId = workspaceScope(first.chatId);
  const projectAgentsBeforeRelease = await session.api(
    `/api/v1/vfs/content?wf_id=${encodeURIComponent(scopeId)}`
      + `&path=${encodeURIComponent('/data/AGENTS.md')}`,
  ).then((response) => response.json()) as { content: string };
  const projectMemoryBeforeRelease = await session.api(
    `/api/v1/vfs/content?wf_id=${encodeURIComponent(scopeId)}`
      + `&path=${encodeURIComponent('/data/MEMORY.md')}`,
  ).then((response) => response.json()) as { content: string };
  expect(projectAgentsBeforeRelease.content).toContain(marker);
  expect(projectMemoryBeforeRelease.content).toContain(marker);

  const released = await session.api(
    `/api/v1/chats/sandbox?chat_id=${encodeURIComponent(first.chatId)}`,
    { method: 'DELETE' },
  ).then((response) => response.json()) as { status: string };
  expect(released.status).toBe('closed');
  const confirmedClosed = await session.api(
    `/api/v1/chats/sandbox?chat_id=${encodeURIComponent(first.chatId)}`,
  ).then((response) => response.json()) as { status: string };
  expect(confirmedClosed.status).toBe('closed');

  // Releasing destroys the sandbox and its plaintext Runtime projection. The
  // encrypted Object Store snapshot is the durable authority and is restored
  // into a new private POSIX directory for the next Turn.

  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.locator(`button[data-chat-id="${first.chatId}"]`).click();
  const second = await send(
    page,
    'What private resume marker did I ask you to remember in the previous turn? '
      + 'Use filesystem tools to verify /data/AGENTS.md, /data/MEMORY.md, '
      + '/runtime/.codex/AGENTS.md, and /runtime/home/MEMORY.md still exist and contain '
      + 'that marker. End with RUNTIME_FILES_OK. Do not ask me to repeat the marker.',
  );
  expect(second.chatId).toBe(first.chatId);
  await expect(second.answer).toContainText(marker, { timeout: 30_000 });
  const resumedStatus = await session.api(
    `/api/v1/chats/sandbox?chat_id=${encodeURIComponent(first.chatId)}`,
  ).then((response) => response.json()) as { status: string };
  expect(resumedStatus.status).toBe('running');
  await expect(second.answer).toContainText('RUNTIME_FILES_OK', { timeout: 30_000 });
  await second.answer.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath(`${authMode}-codex-vfs-resume-second-turn.png`),
    fullPage: true,
  });

  const projectAgents = await session.api(
    `/api/v1/vfs/content?wf_id=${encodeURIComponent(scopeId)}`
      + `&path=${encodeURIComponent('/data/AGENTS.md')}`,
  ).then((response) => response.json()) as { content: string };
  const projectMemory = await session.api(
    `/api/v1/vfs/content?wf_id=${encodeURIComponent(scopeId)}`
      + `&path=${encodeURIComponent('/data/MEMORY.md')}`,
  ).then((response) => response.json()) as { content: string };
  expect(projectAgents.content).toContain(marker);
  expect(projectMemory.content).toContain(marker);
});
}
