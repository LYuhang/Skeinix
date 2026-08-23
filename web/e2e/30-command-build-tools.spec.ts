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
const PREFIX = 'command-tools-20260803-build';
const MINIMAL_WORKFLOW = {
  node_1: {
    node_id: 'node_1',
    node_type: 'StartNode',
    node_name: '__start__',
    node_description: '',
    input_fields: { x: { type: 'integer', value: 0, reference: '' } },
    output_fields: { x: { type: 'integer', description: '' } },
    node_config: {},
    children: ['node_2'],
    __attributes__: { x: 0, y: 0 },
  },
  node_2: {
    node_id: 'node_2',
    node_type: 'EndNode',
    node_name: '__end__',
    node_description: '',
    input_fields: { y: { type: 'integer', value: 0, reference: '__start__.x' } },
    output_fields: { y: { type: 'integer', description: '' } },
    node_config: {},
    children: [],
    __attributes__: { x: 240, y: 0 },
  },
};

test.setTimeout(2_400_000);

function rows(payload: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(payload)) return payload as Array<Record<string, unknown>>;
  if (payload && typeof payload === 'object') {
    const items = (payload as { items?: unknown }).items;
    if (Array.isArray(items)) return items as Array<Record<string, unknown>>;
  }
  return [];
}

for (const runtime of ['langchain', 'codex'] as const satisfies readonly RealRuntimeName[]) {
  test.describe(`${runtime} /workflow every tool`, () => {
    const session = new E2ECookieSession();
    const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    const workflowName = `${PREFIX}-${runtime}-${unique}`;
    const workflowPath = `/data/command-tools/build-${runtime}/workflow.json`;
    let runtimeProfile: RealRuntimeProfile | null = null;
    let chatId = '';
    let workspaceScopeId = '';

    test.beforeAll(async () => {
      console.log(`[${runtime}-build] registering disposable user`);
      await session.register(`command-build-${runtime}`);
      console.log(`[${runtime}-build] selecting runtime`);
      runtimeProfile = await provisionRealRuntime(session, runtime);
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
      const response = await session.api('/api/v1/workflows', {}, true);
      if (response.ok) {
        for (const workflow of rows(await response.json())) {
          const id = String(workflow.wf_id ?? workflow.id ?? '');
          const name = String(workflow.name ?? workflow.workflow_name ?? '');
          if (id && name.startsWith(PREFIX)) {
            await session.api(`/api/v1/workflows/${encodeURIComponent(id)}`, {
              method: 'DELETE',
            }, true);
          }
        }
      }
      runtimeProfile?.cleanup();
    });

    test.beforeEach(async ({ context }: { context: BrowserContext }) => {
      await session.seed(context, 'en');
    });

    async function openChat(page: Page) {
      page.setDefaultTimeout(30_000);
      console.log(`[${runtime}-build] opening Chat UI`);
      await page.goto('/chat');
      await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
        timeout: 30_000,
      });
      await page.locator('[data-action="chat-new"]').click();
      if (!runtimeProfile) throw new Error(`${runtime} Runtime profile was not provisioned`);
      await selectRuntimeModel(page, runtimeProfile);
      console.log(`[${runtime}-build] confirming automatic tool approval`);
      const optionsToggle = page.locator('[data-role="chat-composer-options-toggle"]');
      await expect(optionsToggle).toBeEnabled();
      await optionsToggle.click();
      const approvalSelect = page.locator('[data-role="chat-approval-mode-select"]');
      await expect(approvalSelect).toHaveCount(0);
      console.log(`[${runtime}-build] composer ready`);
    }

    async function invoke(
      page: Page,
      toolName: string,
      instruction: string,
      marker: string,
      timeout = 360_000,
      expectedOutput?: string | RegExp,
    ) {
      const matchingActivities = page.locator('[data-tool-activity="true"]').filter({
        hasText: toolName,
      });
      const before = await matchingActivities.count();
      const composer = page.locator('[data-role="agent-composer-input"]');
      await composer.fill(`/workflow ${instruction} After that tool succeeds, reply exactly ${marker}.`);
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
      await expect(matchingActivities).toHaveCount(before + 1, { timeout: 60_000 });
      const activity = matchingActivities.last();
      const toggle = activity.locator('[data-action="tool-activity-toggle"]');
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      const calls = activity.locator(
        `[data-role="tool-call"][data-tool-name="${toolName}"]`,
      );
      await expect(calls).toHaveCount(1, { timeout: 30_000 });
      const call = calls.first();
      await expect(call).toHaveAttribute('data-tool-status', /^(done|error)$/, { timeout });
      const status = await call.getAttribute('data-tool-status');
      if (status === 'error') {
        const callToggle = call.locator('[data-action="tool-call-toggle"]');
        if (await callToggle.getAttribute('aria-expanded') !== 'true') await callToggle.click();
        const output = (await call.locator('[data-role="tool-output"]').innerText()).trim();
        throw new Error(`${toolName} failed through ${runtime}: ${output}`);
      }
      if (expectedOutput !== undefined) {
        const callToggle = call.locator('[data-action="tool-call-toggle"]');
        if (await callToggle.getAttribute('aria-expanded') !== 'true') await callToggle.click();
        await expect(call.locator('[data-role="tool-output"]')).toContainText(expectedOutput);
      }
      await expect(composer).toBeEditable({ timeout });
    }

    async function readVfs(path: string): Promise<string> {
      const response = await session.api(
        `/api/v1/vfs/content?wf_id=${encodeURIComponent(workspaceScopeId)}`
          + `&path=${encodeURIComponent(path)}`,
      );
      const payload = await response.json() as { content: string };
      return payload.content;
    }

    async function writeVfs(path: string, content: string, contentType: string) {
      await session.api('/api/v1/vfs/content', {
        method: 'PUT',
        body: JSON.stringify({
          wf_id: workspaceScopeId,
          path,
          content,
          content_type: contentType,
        }),
      });
    }

    test(`invokes all eleven /workflow tools through ${runtime}`, async ({ page }) => {
      await openChat(page);

      await invoke(
        page,
        'list_workflows',
        'Call list_workflows exactly once with no filters. Do not call another command tool.',
        'BUILD_LIST_OK',
      );
      const workspace = await session.api(
        `/api/v1/chats/workspace?chat_id=${encodeURIComponent(chatId)}`,
      ).then((response) => response.json()) as { workspace_scope_id: string };
      workspaceScopeId = workspace.workspace_scope_id;

      await invoke(
        page,
        'create_workflow',
        `Call create_workflow exactly once with name "${workflowName}" and description `
          + '"Every build tool acceptance". Do not call another command tool.',
        'BUILD_CREATE_OK',
      );
      const workflowList = rows(await session.api('/api/v1/workflows').then((response) => response.json()));
      const created = workflowList.find((workflow) => (
        String(workflow.name ?? workflow.workflow_name ?? '') === workflowName
      ));
      const workflowId = String(created?.wf_id ?? created?.id ?? '');
      expect(workflowId).not.toBe('');

      await invoke(
        page,
        'set_workflow',
        `Call set_workflow exactly once with workflow_id "${workflowId}". `
          + 'Do not call list_workflows or another command tool.',
        'BUILD_SET_OK',
      );
      const binding = await session.api(
        `/api/v1/chats/workspace?chat_id=${encodeURIComponent(chatId)}`,
      ).then((response) => response.json()) as { current_workflow_id: string };
      expect(binding.current_workflow_id).toBe(workflowId);

      await invoke(
        page,
        'get_workflow',
        `Call get_workflow exactly once with workflow_path "${workflowPath}". `
          + 'Do not call another command tool.',
        'BUILD_GET_OK',
      );
      expect(JSON.parse(await readVfs(workflowPath))).toBeTruthy();

      await invoke(
        page,
        'get_node_spec',
        'Call get_node_spec exactly once with node_type "StartNode". '
          + 'Do not call another command tool.',
        'BUILD_SPEC_OK',
      );

      const compactWorkflow = JSON.stringify(MINIMAL_WORKFLOW);
      await writeVfs(workflowPath, compactWorkflow, 'application/json');
      await invoke(
        page,
        'check_workflow',
        `Call check_workflow exactly once with workflow_path "${workflowPath}". `
          + 'Do not call any other command tool.',
        'BUILD_CHECK_OK',
        360_000,
        'Validation passed',
      );
      expect(Object.keys(JSON.parse(await readVfs(workflowPath)))).toEqual(
        expect.arrayContaining(['node_1', 'node_2']),
      );

      await invoke(
        page,
        'update_canvas',
        `Call update_canvas exactly once with workflow_path "${workflowPath}" and `
          + 'require_valid=true. Do not call another command tool.',
        'BUILD_CANVAS_OK',
      );

      await invoke(
        page,
        'new_version',
        'Call new_version exactly once. Do not call another command tool.',
        'BUILD_VERSION_OK',
      );
      const versioned = await session.api(`/api/v1/workflows/${encodeURIComponent(workflowId)}`)
        .then((response) => response.json()) as { meta: { active_v: number } };
      expect(versioned.meta.active_v).toBe(2);

      const nodeOutput = `/data/command-tools/build-${runtime}/node-result.json`;
      await invoke(
        page,
        'node_execute',
        `Call node_execute exactly once with node "node_1", inputs '{"x":3}', and `
          + `output_path "${nodeOutput}". Do not call another command tool.`,
        'BUILD_NODE_OK',
        480_000,
      );
      expect(JSON.parse(await readVfs(nodeOutput)).node_id).toBe('node_1');

      const batchInput = `/data/command-tools/build-${runtime}/rows.jsonl`;
      const batchOutput = `/data/command-tools/build-${runtime}/batch-results.jsonl`;
      await writeVfs(batchInput, '{"x":1}\n{"x":2}\n', 'table/jsonl');
      await invoke(
        page,
        'batch_execute',
        `Call batch_execute exactly once with input_path "${batchInput}", `
          + `name "${runtime} build acceptance", row_concurrency 2, and `
          + `output_path "${batchOutput}". Do not call another command tool.`,
        'BUILD_BATCH_OK',
        600_000,
      );
      expect((await readVfs(batchOutput)).trim().split('\n')).toHaveLength(2);

      const runOutput = `/data/command-tools/build-${runtime}/workflow-result.json`;
      await invoke(
        page,
        'run_workflow',
        `Call run_workflow exactly once with inputs '{"x":7}' and output_path `
          + `"${runOutput}". Do not call another command tool.`,
        'BUILD_RUN_OK',
        480_000,
      );
      const runResult = JSON.parse(await readVfs(runOutput));
      expect(runResult.status, JSON.stringify(runResult)).toBe('success');

      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.locator(`button[data-chat-id="${chatId}"]`).click();
      await loadCompleteChatHistory(page, chatId);
      const persistedActivities = page.locator('[data-tool-activity="true"]');
      await expect(persistedActivities.first()).toBeVisible({ timeout: 60_000 });
      for (let index = 0; index < await persistedActivities.count(); index += 1) {
        const toggle = persistedActivities.nth(index).locator(
          '[data-action="tool-activity-toggle"]',
        );
        if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      }
      for (const toolName of [
        'list_workflows',
        'create_workflow',
        'set_workflow',
        'get_workflow',
        'get_node_spec',
        'check_workflow',
        'update_canvas',
        'new_version',
        'node_execute',
        'batch_execute',
        'run_workflow',
      ]) {
        await expect(page.locator(
          `[data-role="tool-call"][data-tool-name="${toolName}"][data-tool-status="done"]`,
        )).toHaveCount(1, { timeout: 30_000 });
      }
    });
  });
}
