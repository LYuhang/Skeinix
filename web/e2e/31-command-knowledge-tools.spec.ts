import {
  chmodSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  rmSync,
} from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve, sep } from 'node:path';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

type RuntimeName = 'langchain' | 'codex';

const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/([^/]+)\/chats\/([^/]+)\/messages$/;
const PREFIX = 'command-tools-20260803-knowledge';

test.setTimeout(1_200_000);

for (const runtime of ['langchain', 'codex'] as const satisfies readonly RuntimeName[]) {
  test.describe(`${runtime} /knowledge every tool`, () => {
    const session = new E2ECookieSession();
    const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const kbName = `${PREFIX}-${runtime}-${unique}`;
    const identifier = `KNOWLEDGE_FACT_${runtime.toUpperCase()}_${unique}`;
    const accountRoots: string[] = [];
    let accountModelOption: string | null = null;
    let kbId = '';
    let chatId = '';

    test.beforeAll(async () => {
      console.log(`[${runtime}-knowledge] registering user`);
      await session.register(`command-knowledge-${runtime}`);
      await session.api('/api/v1/agent-runtime/settings', {
        method: 'PUT',
        body: JSON.stringify({ default_runtime_type: runtime }),
        signal: AbortSignal.timeout(60_000),
      });

      if (runtime === 'codex') {
        const source = join(homedir(), '.codex', 'auth.json');
        if (!existsSync(source)) throw new Error(`host Codex identity is missing: ${source}`);
        const me = await session.api('/api/v1/auth/me').then((response) => response.json()) as {
          tenant_id: string;
          user_id: string;
        };
        const runtimeRoot = resolve(
          process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
        );
        const accountRoot = resolve(runtimeRoot, me.tenant_id, me.user_id, 'codex-account-v1');
        if (!accountRoot.startsWith(`${runtimeRoot}${sep}`)) {
          throw new Error('refusing to create Codex identity outside AGENT_RUNTIME_ROOT');
        }
        const accountHome = join(accountRoot, '.codex');
        mkdirSync(accountHome, { recursive: true, mode: 0o700 });
        chmodSync(accountHome, 0o700);
        const destination = join(accountHome, 'auth.json');
        copyFileSync(source, destination);
        chmodSync(destination, 0o600);
        accountRoots.push(accountRoot);

        const capabilities = await session.api('/api/v1/agent-runtime/capabilities', {
          signal: AbortSignal.timeout(120_000),
        }).then((response) => response.json()) as {
          authenticated: boolean | null;
          default_model_id: string | null;
          models: Array<{ id: string; label: string; provider?: string }>;
        };
        expect(capabilities.authenticated).toBe(true);
        const model = capabilities.models.find((option) => option.provider === 'chatgpt')
          ?? capabilities.models.find((option) => (
            option.id === 'codex:default' || option.id.startsWith('codex:managed:')
          ))
          ?? capabilities.models[0];
        if (!model) throw new Error('Codex exposes no configured model');
        accountModelOption = capabilities.default_model_id === model.id
          ? null
          : `${model.label}${model.provider ? ` (${model.provider})` : ''}`;
      }

      console.log(`[${runtime}-knowledge] creating knowledge base`);
      const created = await session.api('/api/v1/kb', {
        method: 'POST',
        body: JSON.stringify({
          name: kbName,
          description: 'Every Knowledge tool acceptance',
        }),
      }).then((response) => response.json()) as { id: string };
      kbId = created.id;

      console.log(`[${runtime}-knowledge] uploading deterministic Markdown`);
      const form = new FormData();
      form.append('file', new File([
        `# Command tool fact\n\nThe exact verification identifier is ${identifier}.\n`,
      ], 'acceptance.md', { type: 'text/markdown' }));
      await session.form(`/api/v1/kb/${encodeURIComponent(kbId)}/files`, form);
      console.log(`[${runtime}-knowledge] waiting for indexed status`);
      await expect.poll(async () => {
        const files = await session.api(`/api/v1/kb/${encodeURIComponent(kbId)}/files`)
          .then((response) => response.json()) as Array<{ status: string }>;
        return files[0]?.status;
      }, { timeout: 240_000 }).toBe('indexed');
      console.log(`[${runtime}-knowledge] index ready`);
    });

    test.afterAll(async () => {
      if (chatId) {
        const bootstrap = await session.api('/api/v1/chats/bootstrap', {}, true);
        if (bootstrap.ok) {
          const scope = await bootstrap.json() as { carrier_scope_id: string };
          await session.api(
            `/api/v1/chat-scopes/${encodeURIComponent(scope.carrier_scope_id)}`
              + `/chats/${encodeURIComponent(chatId)}`,
            { method: 'DELETE' },
            true,
          );
        }
      }
      if (kbId) {
        await session.api(`/api/v1/kb/${encodeURIComponent(kbId)}`, {
          method: 'DELETE',
        }, true);
      }
      for (const accountRoot of accountRoots) {
        const runtimeRoot = resolve(
          process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
        );
        if (resolve(accountRoot).startsWith(`${runtimeRoot}${sep}`)) {
          rmSync(accountRoot, { recursive: true, force: true });
        }
      }
    });

    test.beforeEach(async ({ context }: { context: BrowserContext }) => {
      await session.seed(context, 'en');
    });

    async function openChat(page: Page) {
      console.log(`[${runtime}-knowledge] opening Chat UI`);
      await page.goto('/chat');
      await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
        timeout: 30_000,
      });
      await page.locator('[data-action="chat-new"]').click();
      if (runtime === 'codex' && accountModelOption) {
        await page.locator('[data-role="chat-model-select"]').click();
        const option = page.getByRole('option', { name: accountModelOption, exact: true });
        await expect(option).toBeVisible({ timeout: 30_000 });
        await option.click();
      }
      await page.locator('[data-role="chat-composer-options-toggle"]').click();
      await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
      console.log(`[${runtime}-knowledge] composer ready`);
    }

    async function invoke(
      page: Page,
      toolName: string,
      instruction: string,
      marker: string,
      expectedOutput: string,
    ) {
      const activities = page.locator('[data-tool-activity="true"]').filter({ hasText: toolName });
      const before = await activities.count();
      await page.locator('[data-role="agent-composer-input"]').fill(
        `/knowledge ${instruction} After that tool succeeds, reply exactly ${marker}.`,
      );
      const [response] = await Promise.all([
        page.waitForResponse((candidate) => (
          candidate.request().method() === 'POST'
            && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
        ), { timeout: 30_000 }),
        page.locator('[data-action="agent-composer-send"]').click(),
      ]);
      expect(response.ok()).toBe(true);
      const match = new URL(response.url()).pathname.match(MESSAGE_PATH);
      expect(match).not.toBeNull();
      chatId ||= match![2];
      await expect(activities).toHaveCount(before + 1, { timeout: 360_000 });
      const activity = activities.last();
      const activityToggle = activity.locator('[data-action="tool-activity-toggle"]');
      if (await activityToggle.getAttribute('aria-expanded') !== 'true') await activityToggle.click();
      const call = activity.locator(
        `[data-role="tool-call"][data-tool-name="${toolName}"]`,
      ).first();
      await expect(call).toHaveAttribute('data-tool-status', /^(done|error)$/, { timeout: 360_000 });
      if (await call.getAttribute('data-tool-status') === 'error') {
        const toggle = call.locator('[data-action="tool-call-toggle"]');
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
        throw new Error(`${toolName} failed: ${await call.locator('[data-role="tool-output"]').innerText()}`);
      }
      const toggle = call.locator('[data-action="tool-call-toggle"]');
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await expect(call.locator('[data-role="tool-output"]')).toContainText(expectedOutput, {
        timeout: 30_000,
      });
      await expect(
        page.locator('[data-message-role="assistant"]').filter({ hasText: marker }).last(),
      ).toBeVisible({ timeout: 360_000 });
    }

    test(`invokes all three /knowledge tools through ${runtime}`, async ({ page }) => {
      await openChat(page);
      await invoke(
        page,
        'list_knowledge_bases',
        'Call list_knowledge_bases exactly once. Do not call another command tool.',
        'KNOWLEDGE_LIST_OK',
        kbName,
      );
      await invoke(
        page,
        'get_knowledge_base',
        `Call get_knowledge_base exactly once with kb_id "${kbId}". `
          + 'Do not call another command tool.',
        'KNOWLEDGE_GET_OK',
        kbId,
      );
      await invoke(
        page,
        'search_knowledge',
        `Call search_knowledge exactly once with kb_ids ["${kbId}"], query `
          + `"${identifier}", top_k 5. `
          + 'Do not call another command tool.',
        'KNOWLEDGE_SEARCH_OK',
        identifier,
      );

      await page.reload({ waitUntil: 'domcontentloaded' });
      const activities = page.locator('[data-tool-activity="true"]');
      await expect(activities).toHaveCount(3, { timeout: 60_000 });
      for (let index = 0; index < 3; index += 1) {
        const toggle = activities.nth(index).locator('[data-action="tool-activity-toggle"]');
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      }
      for (const tool of [
        'list_knowledge_bases',
        'get_knowledge_base',
        'search_knowledge',
      ]) {
        await expect(page.locator(
          `[data-role="tool-call"][data-tool-name="${tool}"][data-tool-status="done"]`,
        )).toHaveCount(1, { timeout: 30_000 });
      }
    });
  });
}
