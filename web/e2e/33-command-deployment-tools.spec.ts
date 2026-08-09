import { chmodSync, copyFileSync, existsSync, mkdirSync, rmSync } from 'node:fs';
import { homedir } from 'node:os';
import { join, resolve, sep } from 'node:path';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

type RuntimeName = 'langchain' | 'codex';
type DeploymentRow = {
  id: string;
  name: string;
  slug: string;
  enabled: boolean;
  rate_limit_qps: number;
};

const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/([^/]+)\/chats\/([^/]+)\/messages$/;
const PREFIX = 'command-tools-20260803-deployment';
const MINIMAL_WORKFLOW = {
  node_1: {
    node_id: 'node_1', node_type: 'StartNode', node_name: '__start__', node_description: '',
    input_fields: { x: { type: 'integer', value: 0, reference: '' } },
    output_fields: { x: { type: 'integer', description: '' } }, node_config: {},
    children: ['node_2'], __attributes__: { x: 0, y: 0 },
  },
  node_2: {
    node_id: 'node_2', node_type: 'EndNode', node_name: '__end__', node_description: '',
    input_fields: { y: { type: 'integer', value: 0, reference: '__start__.x' } },
    output_fields: { y: { type: 'integer', description: '' } }, node_config: {},
    children: [], __attributes__: { x: 240, y: 0 },
  },
};

test.setTimeout(1_800_000);

for (const runtime of ['langchain', 'codex'] as const satisfies readonly RuntimeName[]) {
  test.describe(`${runtime} /deployment every tool`, () => {
    const session = new E2ECookieSession();
    const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const workflowName = `${PREFIX}-workflow-${runtime}-${unique}`;
    const deploymentName = `${PREFIX}-${runtime}-${unique}`;
    const updatedName = `${deploymentName}-updated`;
    const slug = `accept-${runtime}-${Date.now().toString(36)}-${Math.random()
      .toString(36).slice(2, 8)}`;
    const accountRoots: string[] = [];
    let accountModelLabel: string | null = null;
    let workflowId = '';
    let deploymentId = '';
    let chatId = '';

    test.beforeAll(async () => {
      console.log(`[${runtime}-deployment] registering disposable user`);
      await session.register(`command-deployment-${runtime}`);
      await session.api('/api/v1/agent-runtime/settings', {
        method: 'PUT',
        body: JSON.stringify({ default_runtime_type: runtime }),
      });
      if (runtime === 'codex') {
        const source = join(homedir(), '.codex', 'auth.json');
        if (!existsSync(source)) throw new Error(`host Codex identity is missing: ${source}`);
        const me = await session.api('/api/v1/auth/me').then((response) => response.json()) as {
          tenant_id: string; user_id: string;
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
        copyFileSync(source, join(accountHome, 'auth.json'));
        chmodSync(join(accountHome, 'auth.json'), 0o600);
        accountRoots.push(accountRoot);
        const capabilities = await session.api('/api/v1/agent-runtime/capabilities')
          .then((response) => response.json()) as {
            authenticated: boolean | null;
            default_model_id: string | null;
            models: Array<{ id: string; label: string; provider?: string }>;
          };
        expect(capabilities.authenticated).toBe(true);
        const model = capabilities.models.find((candidate) => candidate.provider === 'chatgpt')
          ?? capabilities.models.find((candidate) => (
            candidate.id === 'codex:default' || candidate.id.startsWith('codex:managed:')
          ))
          ?? capabilities.models[0];
        if (!model) throw new Error('Codex exposes no configured model');
        accountModelLabel = capabilities.default_model_id === model.id
          ? null
          : `${model.label}${model.provider ? ` (${model.provider})` : ''}`;
      }
      const workflow = await session.api('/api/v1/workflows', {
        method: 'POST',
        body: JSON.stringify({ name: workflowName, description: 'Deployment acceptance', tags: [] }),
      }).then((response) => response.json()) as { wf_id: string };
      workflowId = workflow.wf_id;
      await session.api(`/api/v1/workflows/${encodeURIComponent(workflowId)}/commits`, {
        method: 'POST',
        body: JSON.stringify({ workflow: MINIMAL_WORKFLOW, note: 'deployment acceptance fixture' }),
      });
    });

    test.afterAll(async () => {
      if (deploymentId) {
        await session.api(`/api/v1/deployments/${encodeURIComponent(deploymentId)}`, {
          method: 'DELETE',
        }, true);
      }
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
      if (workflowId) {
        await session.api(`/api/v1/workflows/${encodeURIComponent(workflowId)}`, {
          method: 'DELETE',
        }, true);
      }
      for (const accountRoot of accountRoots) {
        const root = resolve(
          process.env.AGENT_RUNTIME_ROOT ?? join(homedir(), '.vibecanvas', 'agent-runtime'),
        );
        if (resolve(accountRoot).startsWith(`${root}${sep}`)) {
          rmSync(accountRoot, { recursive: true, force: true });
        }
      }
    });

    test.beforeEach(async ({ context }: { context: BrowserContext }) => {
      await session.seed(context, 'en');
    });

    async function openChat(page: Page) {
      await page.goto('/chat');
      await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({ timeout: 30_000 });
      await page.locator('[data-action="chat-new"]').click();
      if (runtime === 'codex' && accountModelLabel) {
        await page.locator('[data-role="chat-model-select"]').click();
        await page.getByRole('option', { name: accountModelLabel, exact: true }).click();
      }
      await page.locator('[data-role="chat-composer-options-toggle"]').click();
      await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
      await expect(page.locator('[data-role="agent-composer-input"]')).toBeEditable({
        timeout: 30_000,
      });
      console.log(`[${runtime}-deployment] composer ready`);
    }

    async function invoke(
      page: Page,
      toolName: string,
      instruction: string,
      marker: string,
      timeout = 360_000,
    ) {
      console.log(`[${runtime}-deployment] invoking ${toolName}`);
      const activities = page.locator('[data-tool-activity="true"]').filter({ hasText: toolName });
      const before = await activities.count();
      const composer = page.locator('[data-role="agent-composer-input"]');
      await expect(composer).toBeEditable({ timeout: 30_000 });
      await composer.fill(
        `/deployment ${instruction} After that tool succeeds, reply exactly ${marker}.`,
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
      await expect(activities).toHaveCount(before + 1, { timeout: 90_000 });
      const activity = activities.last();
      const activityToggle = activity.locator('[data-action="tool-activity-toggle"]');
      if (await activityToggle.getAttribute('aria-expanded') !== 'true') await activityToggle.click();
      const calls = activity.locator(`[data-role="tool-call"][data-tool-name="${toolName}"]`);
      await expect(calls).toHaveCount(1, { timeout: 30_000 });
      const call = calls.first();
      await expect(call).toHaveAttribute('data-tool-status', /^(done|error)$/, { timeout });
      if (await call.getAttribute('data-tool-status') === 'error') {
        const toggle = call.locator('[data-action="tool-call-toggle"]');
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
        throw new Error(
          `${toolName} failed through ${runtime}: `
            + (await call.locator('[data-role="tool-output"]').innerText()).trim(),
        );
      }
      // Natural-language wording is not part of the tool contract. The unique
      // completed card plus the durable REST assertion is authoritative; only
      // wait for the Turn to return to the send state before starting another.
      await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible({
        timeout: 60_000,
      });
    }

    test(`invokes all five /deployment tools through ${runtime}`, async ({ page }) => {
      await openChat(page);
      await invoke(
        page,
        'deployment_create',
        `Call deployment_create exactly once with workflow_id "${workflowId}", name `
          + `"${deploymentName}", slug "${slug}", trigger_type "api", version_pin "head", `
          + 'and rate_limit_qps 3. Do not call another command tool.',
        'DEPLOYMENT_CREATE_OK',
      );
      const listed = await session.api(
        `/api/v1/deployments?workflow_id=${encodeURIComponent(workflowId)}&limit=100&offset=0`,
      ).then((response) => response.json()) as { items: DeploymentRow[] };
      const created = listed.items.find((item) => item.slug === slug);
      expect(created).toBeTruthy();
      deploymentId = created!.id;

      await invoke(
        page,
        'deployment_list',
        `Call deployment_list exactly once with workflow_id "${workflowId}", limit 10 and offset 0. `
          + 'Do not call another command tool.',
        'DEPLOYMENT_LIST_OK',
      );
      await invoke(
        page,
        'deployment_get',
        `Call deployment_get exactly once with deployment_id "${deploymentId}". `
          + 'Do not call another command tool.',
        'DEPLOYMENT_GET_OK',
      );
      await invoke(
        page,
        'deployment_update',
        `The exact deployment was inspected in the previous Turn. Call deployment_update exactly `
          + `once with deployment_id "${deploymentId}", name "${updatedName}", enabled false, `
          + 'and rate_limit_qps 7. Do not call another command tool.',
        'DEPLOYMENT_UPDATE_OK',
      );
      const updated = await session.api(`/api/v1/deployments/${encodeURIComponent(deploymentId)}`)
        .then((response) => response.json()) as DeploymentRow;
      expect(updated).toMatchObject({ name: updatedName, enabled: false, rate_limit_qps: 7 });

      await invoke(
        page,
        'deployment_delete',
        `The exact disabled deployment was inspected and updated in the previous Turns. Call `
          + `deployment_delete exactly once with deployment_id "${deploymentId}". `
          + 'Do not call another command tool.',
        'DEPLOYMENT_DELETE_OK',
      );
      expect((await session.api(
        `/api/v1/deployments/${encodeURIComponent(deploymentId)}`,
        {},
        true,
      )).status).toBe(404);
      deploymentId = '';

      await page.reload({ waitUntil: 'domcontentloaded' });
      const persisted = page.locator('[data-tool-activity="true"]');
      await expect(persisted).toHaveCount(5, { timeout: 60_000 });
      for (let index = 0; index < 5; index += 1) {
        const toggle = persisted.nth(index).locator('[data-action="tool-activity-toggle"]');
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      }
      for (const tool of [
        'deployment_create', 'deployment_list', 'deployment_get',
        'deployment_update', 'deployment_delete',
      ]) {
        await expect(page.locator(
          `[data-role="tool-call"][data-tool-name="${tool}"][data-tool-status="done"]`,
        )).toHaveCount(1, { timeout: 30_000 });
      }
    });
  });
}
