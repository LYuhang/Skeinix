import { readFileSync } from 'node:fs';

import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

test.setTimeout(900_000);

const session = new E2ECookieSession();
let modelName = '';

test.beforeAll(async () => {
  await session.register('five-node-workflow-real');
  const payload = await session.api('/api/v1/enums').then((response) => response.json()) as {
    enums?: { model_names?: string[] };
  };
  modelName = payload.enums?.model_names?.[0] ?? '';
  if (!modelName) throw new Error('no configured Workflow model is available');
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'en');
});

function fiveNodeWorkflow(model: string) {
  return {
    __meta__: {
      acceptance_marker: 'FIVE_NODE_META_ROUND_TRIP',
      code_requirements: [],
    },
    node_1: {
      node_id: 'node_1',
      node_name: '__start__',
      node_type: 'StartNode',
      node_description: 'Receive the release topic.',
      input_fields: { topic: { type: 'string', value: '', reference: '' } },
      output_fields: { topic: { type: 'string', description: 'Release topic' } },
      node_config: {},
      children: ['node_2'],
      __attributes__: { x: 0, y: 160 },
    },
    node_2: {
      node_id: 'node_2',
      node_name: 'Generate',
      node_type: 'PromptNode',
      node_description: 'Generate one deterministic release summary.',
      input_fields: {
        topic: { type: 'string', value: '', reference: '__start__.topic' },
      },
      output_fields: { summary: { type: 'string', description: 'Release summary' } },
      node_config: {
        prompt_template: [
          '# Task',
          'Return a concise release marker for {{topic}}.',
          '# Output Format',
          'Return only this JSON object: {"summary":"INITIAL_RELEASE_READY"}',
        ].join('\n'),
        model_name: model,
        inference_config: {
          temperature: 0,
          max_tokens: 128,
          top_k: -1,
          top_p: 1,
        },
      },
      children: ['node_3'],
      __attributes__: { x: 280, y: 160 },
    },
    node_3: {
      node_id: 'node_3',
      node_name: 'Measure',
      node_type: 'CodeNode',
      node_description: 'Normalize the generated marker.',
      input_fields: {
        summary: { type: 'string', value: '', reference: 'Generate.summary' },
      },
      output_fields: {
        processed: { type: 'string', description: 'Normalized marker' },
        length: { type: 'integer', description: 'Marker length' },
      },
      node_config: {
        programming_language: 'python',
        process_fn: [
          'def process_fn(inputs):',
          '    value = str(inputs.get("summary", "")).strip().upper()',
          '    return {"processed": value, "length": len(value)}',
        ].join('\n'),
      },
      children: ['node_4'],
      __attributes__: { x: 560, y: 160 },
    },
    node_4: {
      node_id: 'node_4',
      node_name: 'Route',
      node_type: 'ConditionNode',
      node_description: 'Route to the release output.',
      input_fields: {
        length: { type: 'integer', value: 0, reference: 'Measure.length' },
      },
      output_fields: { condition: { type: 'string', description: 'Matched route' } },
      node_config: {
        conditions: [
          { condition_name: 'others', condition_str: 'others', next_node_id: 'node_5' },
        ],
      },
      children: ['node_5'],
      __attributes__: { x: 840, y: 160 },
    },
    node_5: {
      node_id: 'node_5',
      node_name: '__end__',
      node_type: 'EndNode',
      node_description: 'Return the normalized release marker.',
      input_fields: {
        processed: { type: 'string', value: '', reference: 'Measure.processed' },
      },
      output_fields: {
        processed: { type: 'string', description: 'Final release marker' },
      },
      node_config: {},
      children: [],
      __attributes__: { x: 1120, y: 160 },
    },
  };
}

async function createWorkflowFromPage(page: Page): Promise<string> {
  page.setDefaultTimeout(30_000);
  console.log('[five-node] opening workspace');
  await page.goto('/workspace');
  await expect(page.getByRole('heading', { name: 'Workflows', exact: true })).toBeVisible();
  console.log('[five-node] opening create dialog');
  await page.getByRole('button', { name: /new workflow/i }).click();
  await page.getByTestId('create-workflow-name').fill(`Five node release ${Date.now()}`);
  await page.getByRole('button', { name: 'Create', exact: true }).click();
  await page.waitForURL(/\/workflow\/[^/]+$/);
  console.log('[five-node] workflow created');
  const wfId = new URL(page.url()).pathname.split('/').at(-1) ?? '';
  if (!wfId) throw new Error('workflow id missing after creation');
  return wfId;
}

async function saveAndCheck(page: Page) {
  const save = page.locator('[data-action="canvas-save"]');
  await expect(save).toBeEnabled();
  await save.click();
  await expect(page.getByText('Saved', { exact: true }).last()).toBeVisible({ timeout: 20_000 });
  await page.locator('[data-action="canvas-more"]').click();
  await page.locator('[data-action="check"]').click();
  await expect(page.locator('[data-role="check-ok"]')).toBeVisible({ timeout: 20_000 });
  await page.keyboard.press('Escape');
  await expect(page.locator('[data-role="check-ok"]')).toBeHidden();
}

test('builds, connects, runs, persists and exports Start → Prompt → Code → Condition → End', async ({
  page,
}, testInfo) => {
  const wfId = await createWorkflowFromPage(page);
  const workflow = fiveNodeWorkflow(modelName);
  console.log('[five-node] uploading graph');

  await page.getByTestId('wf-upload-input').setInputFiles({
    name: 'five-node-workflow.json',
    mimeType: 'application/json',
    buffer: Buffer.from(JSON.stringify(workflow)),
  });
  const confirm = page.getByTestId('upload-confirm-dialog');
  if (await confirm.isVisible().catch(() => false)) {
    await page.locator('[data-action="wf-upload-confirm"]').click();
  }
  await expect(page.locator('.react-flow__node')).toHaveCount(5, { timeout: 20_000 });
  await expect(page.locator('.react-flow__edge')).toHaveCount(4);
  console.log('[five-node] graph rendered');
  for (const nodeType of ['StartNode', 'PromptNode', 'CodeNode', 'ConditionNode', 'EndNode']) {
    await expect(page.locator(`[aria-label^="${nodeType}"]`).first()).toBeVisible();
  }
  await saveAndCheck(page);
  console.log('[five-node] graph saved and checked');
  await page.screenshot({ path: testInfo.outputPath('five-node-connected.png'), fullPage: true });

  const sandboxButton = page.getByRole('button', { name: 'Open workflow sandbox' });
  await expect(sandboxButton).toBeEnabled();
  await sandboxButton.click();
  await expect(page.getByRole('button', { name: 'Close workflow sandbox' })).toBeVisible({
    timeout: 60_000,
  });
  const sandbox = await session.api(`/api/v1/workflows/${encodeURIComponent(wfId)}/sandbox`)
    .then((response) => response.json()) as { status?: string };
  expect(sandbox.status).not.toBe('closed');
  console.log('[five-node] sandbox connected');

  await page.locator('[aria-label="CodeNode Measure"]').click();
  await page.getByTestId('inspector-tab-run-node').click();
  await page.getByTestId('node-exec-summary-input').fill('node_isolation_ready');
  await page.getByTestId('node-exec-run').click();
  await expect(page.getByTestId('node-exec-status')).toHaveText('completed', { timeout: 60_000 });
  await expect(page.getByTestId('node-exec-log')).toContainText('NODE_ISOLATION_READY');
  console.log('[five-node] isolated node completed');
  await page.screenshot({ path: testInfo.outputPath('five-node-code-isolation.png'), fullPage: true });

  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible();
  await page.getByTestId('exec-input-topic-input').fill('initial release');
  await page.locator('[data-action="run-workflow"]').click();
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, { timeout: 180_000 });
  await expect(page.getByTestId('exec-node-card')).toHaveCount(5);
  await expect(page.getByTestId('run-output')).toContainText('INITIAL_RELEASE_READY');
  console.log('[five-node] workflow completed');
  await page.screenshot({ path: testInfo.outputPath('five-node-workflow-completed.png'), fullPage: true });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('.react-flow__node')).toHaveCount(5, { timeout: 30_000 });
  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, { timeout: 30_000 });

  await page.locator('[data-action="canvas-more"]').click();
  const downloadPromise = page.waitForEvent('download');
  await page.locator('[data-action="wf-download"]').click();
  const download = await downloadPromise;
  const path = await download.path();
  expect(path).toBeTruthy();
  const exported = JSON.parse(readFileSync(path!, 'utf8')) as {
    __meta__?: { acceptance_marker?: string; code_requirements?: string[] };
  };
  expect(exported.__meta__?.acceptance_marker).toBe('FIVE_NODE_META_ROUND_TRIP');
  expect(exported.__meta__?.code_requirements).toEqual([]);
});
