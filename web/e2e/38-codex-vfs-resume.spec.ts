import { createHash, randomUUID } from 'node:crypto';
import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { join, relative, resolve, sep } from 'node:path';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

test.setTimeout(900_000);

const session = new E2ECookieSession();
const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/[^/]+\/chats\/([^/]+)\/messages$/;
type AuthMode = 'chatgpt_account' | 'managed_api';
type ModelOption = { id: string; label: string; provider?: string };

const modelOptions = new Map<AuthMode, ModelOption>();
let accountRoot: string | null = null;

function workspaceScope(chatId: string) {
  return `__chatws_v2_${Buffer.from(chatId, 'utf8').toString('base64url')}`;
}

function runtimeVolumeDir(
  runtimeRoot: string,
  tenantId: string,
  userId: string,
  scopeId: string,
) {
  const digest = createHash('sha256')
    .update(`vibecanvas:chat-runtime-volume:v1\0${tenantId}\0${userId}\0${scopeId}`)
    .digest('hex');
  return resolve(runtimeRoot, tenantId, userId, 'chat-runtime-v1', digest);
}

function sessionFiles(runtimeDir: string) {
  const root = join(runtimeDir, '.codex', 'sessions');
  if (!existsSync(root)) return [];
  const found: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) visit(path);
      if (entry.isFile() && entry.name.endsWith('.jsonl')) {
        found.push(relative(root, path));
      }
    }
  };
  visit(root);
  return found.sort();
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
  await session.api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: 'codex' }),
  });
  const source = join(homedir(), '.codex', 'auth.json');
  if (!existsSync(source)) throw new Error(`host Codex identity is missing: ${source}`);
  const me = await session.api('/api/v1/auth/me').then((response) => response.json()) as {
    tenant_id: string;
    user_id: string;
  };
  const runtimeRoot = resolve(
    process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
  );
  accountRoot = resolve(runtimeRoot, me.tenant_id, me.user_id, 'codex-account-v1');
  if (!accountRoot.startsWith(`${runtimeRoot}${sep}`)) {
    throw new Error('refusing to create Codex identity outside AGENT_RUNTIME_ROOT');
  }
  const accountHome = join(accountRoot, '.codex');
  mkdirSync(accountHome, { recursive: true, mode: 0o700 });
  chmodSync(accountHome, 0o700);
  copyFileSync(source, join(accountHome, 'auth.json'));
  chmodSync(join(accountHome, 'auth.json'), 0o600);

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
  if (!account) throw new Error('Codex account exposes no ChatGPT model');
  if (!managed) throw new Error('Codex exposes no enterprise-managed API model');
  modelOptions.set('chatgpt_account', account);
  modelOptions.set('managed_api', managed);
});

test.afterAll(() => {
  if (!accountRoot) return;
  const runtimeRoot = resolve(
    process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
  );
  if (resolve(accountRoot).startsWith(`${runtimeRoot}${sep}`)) {
    rmSync(accountRoot, { recursive: true, force: true });
  }
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'zh');
});

for (const authMode of ['chatgpt_account', 'managed_api'] as const satisfies readonly AuthMode[]) {
test(`Codex ${authMode} restores one Chat from its direct Runtime Volume after sandbox loss`, async ({
    page,
  }, testInfo) => {
  const marker = `CHAT_VFS_RESUME_${randomUUID().replaceAll('-', '').slice(0, 16)}`;
  const me = await session.api('/api/v1/auth/me').then((response) => response.json()) as {
    tenant_id: string;
    user_id: string;
  };
  const runtimeRoot = resolve(
    process.env.VFS_VOLUME_ROOT
      ?? process.env.AGENT_RUNTIME_ROOT
      ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
  );

  await page.goto('/chat', { timeout: 30_000 });
  await page.waitForLoadState('networkidle', { timeout: 30_000 });
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
    timeout: 30_000,
  });
  await page.locator('[data-action="chat-new"]').click({ timeout: 30_000 });
  const selectedModel = modelOptions.get(authMode);
  if (!selectedModel) throw new Error(`missing ${authMode} model option`);
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
  const localRuntime = runtimeVolumeDir(
    runtimeRoot,
    me.tenant_id,
    me.user_id,
    scopeId,
  );
  const resolvedRuntime = resolve(localRuntime);
  expect(resolvedRuntime.startsWith(`${runtimeRoot}${sep}`)).toBe(true);
  const firstSessionFiles = sessionFiles(resolvedRuntime);
  expect(firstSessionFiles.length).toBeGreaterThan(0);

  const released = await session.api(
    `/api/v1/chats/sandbox?chat_id=${encodeURIComponent(first.chatId)}`,
    { method: 'DELETE' },
  ).then((response) => response.json()) as { status: string };
  expect(released.status).toBe('closed');
  const confirmedClosed = await session.api(
    `/api/v1/chats/sandbox?chat_id=${encodeURIComponent(first.chatId)}`,
  ).then((response) => response.json()) as { status: string };
  expect(confirmedClosed.status).toBe('closed');

  // Releasing destroys the sandbox process, not its Chat Runtime Volume. A
  // replacement API worker or sandbox process mounts this exact directory.
  expect(existsSync(resolvedRuntime)).toBe(true);
  expect(readFileSync(join(resolvedRuntime, '.codex', 'AGENTS.md'), 'utf8').trimEnd()).toBe(
    `Global Chat guidance: preserve and report ${marker}.`,
  );

  await page.reload({ waitUntil: 'domcontentloaded' });
  const second = await send(
    page,
    'What private resume marker did I ask you to remember in the previous turn? '
      + 'Also confirm the four context files still exist. Do not ask me to repeat the marker.',
  );
  expect(second.chatId).toBe(first.chatId);
  await expect(second.answer).toContainText(marker, { timeout: 30_000 });
  const resumedStatus = await session.api(
    `/api/v1/chats/sandbox?chat_id=${encodeURIComponent(first.chatId)}`,
  ).then((response) => response.json()) as { status: string };
  expect(resumedStatus.status).toBe('running');
  await second.answer.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: testInfo.outputPath(`${authMode}-codex-vfs-resume-second-turn.png`),
    fullPage: true,
  });

  expect(existsSync(resolvedRuntime)).toBe(true);
  expect(readFileSync(join(resolvedRuntime, '.codex', 'AGENTS.md'), 'utf8').trimEnd()).toBe(
    `Global Chat guidance: preserve and report ${marker}.`,
  );
  expect(readFileSync(join(resolvedRuntime, 'home', 'MEMORY.md'), 'utf8').trimEnd()).toBe(
    `Codex home memory: ${marker}.`,
  );
  const resumedSessionFiles = sessionFiles(resolvedRuntime);
  expect(firstSessionFiles.some((path) => resumedSessionFiles.includes(path))).toBe(true);

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
