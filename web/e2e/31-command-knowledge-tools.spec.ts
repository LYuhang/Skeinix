import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';
import {
  loadCompleteChatHistory,
  provisionRealRuntime,
  selectRuntimeModel,
  type RealRuntimeName,
  type RealRuntimeProfile,
} from './real-runtime-profile';

const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/([^/]+)\/chats\/([^/]+)\/messages$/;
const PREFIX = 'command-tools-20260823-knowledge-package';

type KnowledgeRow = {
  id: string;
  name: string;
  package_version: number;
};

test.setTimeout(2_400_000);

for (const runtime of ['langchain', 'codex'] as const satisfies readonly RealRuntimeName[]) {
  test.describe(`${runtime} /knowledge package lifecycle tools`, () => {
    const session = new E2ECookieSession();
    const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const packageName = `${PREFIX}-${runtime}-${unique}`;
    const initialIdentifier = `KNOWLEDGE_INITIAL_${runtime.toUpperCase()}_${unique}`;
    const updatedIdentifier = `KNOWLEDGE_UPDATED_${runtime.toUpperCase()}_${unique}`;
    const sourcePath = `/data/knowledge-contract-${runtime}-${unique}`;
    const reopenedPath = `${sourcePath}-reopened-v2`;
    let profile: RealRuntimeProfile | undefined;
    let kbId = '';
    let chatId = '';

    test.beforeAll(async () => {
      console.log(`[${runtime}-knowledge] registering real Runtime user`);
      await session.register(`command-knowledge-${runtime}`);
      profile = await provisionRealRuntime(session, runtime);
    });

    test.afterAll(async () => {
      try {
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
      } finally {
        profile?.cleanup();
      }
    });

    test.beforeEach(async ({ context }: { context: BrowserContext }) => {
      await session.seed(context, 'en');
    });

    async function listPackages(): Promise<KnowledgeRow[]> {
      return session.api('/api/v1/kb')
        .then((response) => response.json()) as Promise<KnowledgeRow[]>;
    }

    async function waitForPackageVersion(version: number) {
      await expect.poll(async () => {
        const row = (await listPackages()).find((item) => item.id === kbId);
        return row?.package_version;
      }, { timeout: 120_000 }).toBe(version);
    }

    async function waitForIndexedPackage() {
      await expect.poll(async () => {
        const files = await session.api(`/api/v1/kb/${encodeURIComponent(kbId)}/files`)
          .then((response) => response.json()) as Array<{ status: string }>;
        return files.length > 0 && files.every((file) => file.status === 'indexed');
      }, { timeout: 240_000 }).toBe(true);
    }

    async function openChat(page: Page) {
      if (!profile) throw new Error(`${runtime} Runtime profile was not provisioned`);
      console.log(`[${runtime}-knowledge] opening Chat UI`);
      await page.goto('/chat');
      await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
        timeout: 30_000,
      });
      await page.locator('[data-action="chat-new"]').click();
      await selectRuntimeModel(page, profile);
      await page.locator('[data-role="chat-composer-options-toggle"]').click();
      await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
    }

    async function invoke(
      page: Page,
      toolName: string,
      instruction: string,
      marker: string,
      expectedOutput: string,
      expectedStatus: 'done' | 'error' = 'done',
    ) {
      console.log(`[${runtime}-knowledge] invoking ${toolName}`);
      const activities = page.locator('[data-tool-activity="true"]').filter({ hasText: toolName });
      const before = await activities.count();
      const composer = page.locator('[data-role="agent-composer-input"]');
      await expect(composer).toBeEditable({ timeout: 30_000 });
      await composer.fill(`/knowledge ${instruction} Finally, reply exactly ${marker}.`);
      const [response] = await Promise.all([
        page.waitForResponse((candidate) => (
          candidate.request().method() === 'POST'
            && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
        ), { timeout: 30_000 }),
        page.locator('[data-action="agent-composer-send"]').click(),
      ]);
      if (!response.ok()) {
        throw new Error(`${toolName} Turn rejected: ${response.status()} ${await response.text()}`);
      }
      const match = new URL(response.url()).pathname.match(MESSAGE_PATH);
      if (!match) throw new Error(`${toolName} response did not contain a Chat id`);
      chatId ||= match[2];

      await expect(activities).toHaveCount(before + 1, { timeout: 360_000 });
      const activity = activities.last();
      const activityToggle = activity.locator('[data-action="tool-activity-toggle"]');
      if (await activityToggle.getAttribute('aria-expanded') !== 'true') {
        await activityToggle.click();
      }
      const calls = activity.locator(
        `[data-role="tool-call"][data-tool-name="${toolName}"]`,
      );
      await expect(calls).toHaveCount(1, { timeout: 30_000 });
      const call = calls.first();
      await expect(call).toHaveAttribute('data-tool-status', expectedStatus, {
        timeout: 360_000,
      });
      const toggle = call.locator('[data-action="tool-call-toggle"]');
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await expect(call.locator('[data-role="tool-output"]')).toContainText(expectedOutput, {
        timeout: 30_000,
      });
      // The tool result and the composer lifecycle are deterministic product
      // contracts. Do not make the gate depend on the model echoing a marker
      // verbatim after it has already completed the requested operation.
      await expect(composer).toBeEditable({ timeout: 360_000 });
      console.log(`[${runtime}-knowledge] completed ${toolName} (${expectedStatus})`);
    }

    test(`runs all six package tools, conflict handling, and reopen through ${runtime}`, async ({
      page,
    }) => {
      await openChat(page);

      await invoke(
        page,
        'knowledge_create',
        `Use ordinary filesystem tools to create directory "${sourcePath}" with a root `
          + 'README.md that documents a notes/ directory and a notes/fact.md file containing '
          + `exactly "${initialIdentifier}". Then call knowledge_create exactly once with `
          + `name "${packageName}", description "Real Runtime package lifecycle", and `
          + `source_path "${sourcePath}". Do not call another knowledge_* tool.`,
        'KNOWLEDGE_CREATE_OK',
        packageName,
      );
      await expect.poll(async () => (
        (await listPackages()).find((item) => item.name === packageName)?.id ?? ''
      ), { timeout: 120_000 }).not.toBe('');
      const created = (await listPackages()).find((item) => item.name === packageName);
      if (!created) throw new Error('knowledge_create did not persist its package');
      kbId = created.id;
      expect(created.package_version).toBe(1);
      await waitForIndexedPackage();

      await invoke(
        page,
        'knowledge_list',
        'Call knowledge_list exactly once. Do not call another knowledge_* tool.',
        'KNOWLEDGE_LIST_OK',
        packageName,
      );

      await invoke(
        page,
        'knowledge_get',
        `Call knowledge_get exactly once with kb_id "${kbId}" and destination_path `
          + `"${sourcePath}". Do not call another knowledge_* tool.`,
        'KNOWLEDGE_GET_V1_OK',
        '"package_version":1',
      );

      await invoke(
        page,
        'knowledge_update',
        `Use ordinary filesystem tools to replace "${sourcePath}/notes/fact.md" with the `
          + `exact text "${updatedIdentifier}" and update the root README.md so it still `
          + 'describes the final tree. Then call knowledge_update exactly once with '
          + `kb_id "${kbId}", source_path "${sourcePath}", and expected_version 1. `
          + 'Do not call another knowledge_* tool.',
        'KNOWLEDGE_UPDATE_OK',
        '"package_version":2',
      );
      await waitForPackageVersion(2);
      await waitForIndexedPackage();

      await invoke(
        page,
        'knowledge_update',
        `Call knowledge_update exactly once with kb_id "${kbId}", source_path `
          + `"${sourcePath}", and the deliberately stale expected_version 1. This call must `
          + 'fail with a version conflict; do not retry it and do not call another '
          + 'knowledge_* tool.',
        'KNOWLEDGE_CONFLICT_OK',
        'knowledge_version_conflict',
        'error',
      );
      await waitForPackageVersion(2);

      await invoke(
        page,
        'knowledge_search',
        `Call knowledge_search exactly once with kb_ids ["${kbId}"], query `
          + `"${updatedIdentifier}", and top_k 5. Do not call another knowledge_* tool.`,
        'KNOWLEDGE_SEARCH_OK',
        updatedIdentifier,
      );

      await invoke(
        page,
        'knowledge_get',
        `Call knowledge_get exactly once with kb_id "${kbId}" and a new destination_path `
          + `"${reopenedPath}". Then use one ordinary filesystem tool to read `
          + `"${reopenedPath}/notes/fact.md". Do not call another knowledge_* tool.`,
        'KNOWLEDGE_REOPEN_OK',
        reopenedPath,
      );
      const reopenedActivity = page.locator('[data-tool-activity="true"]').last();
      const reopenedActivityToggle = reopenedActivity.locator(
        '[data-action="tool-activity-toggle"]',
      );
      if (await reopenedActivityToggle.getAttribute('aria-expanded') !== 'true') {
        await reopenedActivityToggle.click();
      }
      // Codex exposes its native filesystem operation as `shell`; LangChain
      // may use either the platform `read_file` tool or `shell`. The durable
      // contract is the file content, not a Runtime-specific private tool.
      const reopenedRead = reopenedActivity.locator(
        '[data-role="tool-call"][data-tool-name="read_file"], '
          + '[data-role="tool-call"][data-tool-name="shell"]',
      ).last();
      await expect(reopenedRead).toHaveAttribute('data-tool-status', 'done', {
        timeout: 60_000,
      });
      const reopenedReadToggle = reopenedRead.locator('[data-action="tool-call-toggle"]');
      if (await reopenedReadToggle.getAttribute('aria-expanded') !== 'true') {
        await reopenedReadToggle.click();
      }
      await expect(reopenedRead.locator('[data-role="tool-output"]'))
        .toContainText(updatedIdentifier, { timeout: 60_000 });

      await invoke(
        page,
        'knowledge_delete',
        `I explicitly request deletion of Knowledge package "${packageName}". Call `
          + `knowledge_delete exactly once with kb_id "${kbId}" and confirm true. `
          + 'Do not call another knowledge_* tool.',
        'KNOWLEDGE_DELETE_OK',
        '"status":"deleted"',
      );
      await expect.poll(async () => (
        (await listPackages()).some((item) => item.id === kbId)
      ), { timeout: 120_000 }).toBe(false);

      // Reloading the transcript is the persistence half of the contract: both
      // materializations and the stale-version failure remain independently
      // observable rather than collapsing into a legacy flat/read-only card.
      await page.reload({ waitUntil: 'domcontentloaded' });
      await loadCompleteChatHistory(page, chatId);
      const persistedActivities = page.locator('[data-tool-activity="true"]');
      for (let index = 0; index < await persistedActivities.count(); index += 1) {
        const toggle = persistedActivities.nth(index).locator(
          '[data-action="tool-activity-toggle"]',
        );
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      }
      const expectedCounts: Record<string, number> = {
        knowledge_create: 1,
        knowledge_list: 1,
        knowledge_get: 2,
        knowledge_update: 2,
        knowledge_search: 1,
        knowledge_delete: 1,
      };
      for (const [toolName, count] of Object.entries(expectedCounts)) {
        await expect(page.locator(
          `[data-role="tool-call"][data-tool-name="${toolName}"]`,
        )).toHaveCount(count, { timeout: 60_000 });
      }
      await expect(page.locator(
        '[data-role="tool-call"][data-tool-name="knowledge_update"]'
          + '[data-tool-status="error"]',
      )).toHaveCount(1);
    });
  });
}
