/**
 * Acceptance K — interactive-execution UX walkthrough (Part B of the
 * execution-UX polish task).
 *
 * Validates the complete execution experience end-to-end against
 * the live native stack, screenshotting each working step to
 * `web/screenshots/exec-<NN>-<slug>.png`:
 *
 *   K1  parallel workflow → MULTIPLE nodes breathing simultaneously (the
 *       headline canvas-progress shot), then green-on-finish + completed.
 *   K2  post-run debug — Execution tab per-node status+output; WORKFLOW_SANDBOX
 *       run-folder files; reload → persisted per-node still shows.
 *   K3  Node Execute panel — select node → Execute → run → output log.
 *   K4  Batch — Run Batch → CSV → submit → "View task" hand-off.
 *   K5  Cancel — slow workflow → Execute → Cancel → cancelled state.
 *
 * CodeNode-only graphs → deterministic, no LLM key (the dev default in-process
 * execution path).
 */
import { test, expect, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  registerRealUser,
  seedAuth,
  createWorkflow,
  seedNodes,
  type RealUser,
} from './fixtures';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = path.resolve(__dirname, '..', '..', 'screenshots');

/** Full-page screenshot to `web/screenshots/exec-<name>.png`. */
async function shot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `exec-${name}.png`),
    fullPage: true,
  });
}

let user: RealUser;

test.beforeAll(async () => {
  user = await registerRealUser();
});

test.beforeEach(async ({ context }) => {
  await seedAuth(context, user.token);
});

async function open(page: Page, wfId: string): Promise<void> {
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
}

/**
 * Start → ParallelStart → (branch A: slow CodeNode | branch B: slow CodeNode,
 * writes a /run file) → ParallelEnd → End.
 *
 * Each branch sleeps so the `running` (breathing) state is visible long enough
 * to screenshot, and the two branches run concurrently on the engine's thread
 * pool — so BOTH light up at once.
 */
function parallelSlowNodes(sleepSecs = 2): Record<string, unknown>[] {
  return [
    {
      node_id: 'node_1',
      node_name: '__start__',
      node_type: 'StartNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: {},
      children: ['node_2'],
      __attributes__: { x: 0, y: 120 },
    },
    {
      node_id: 'node_2',
      node_name: 'split',
      node_type: 'ParallelStartNode',
      node_description: 'fan out',
      input_fields: {},
      output_fields: {},
      node_config: {
        branches: {
          a: { branch_description: 'branch A', next_node_id: 'node_3' },
          b: { branch_description: 'branch B', next_node_id: 'node_4' },
        },
        parallel_end_node_id: 'node_5',
      },
      children: ['node_3', 'node_4'],
      __attributes__: { x: 240, y: 120 },
    },
    {
      node_id: 'node_3',
      node_name: 'BranchA',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: {},
      output_fields: { out_a: { type: 'string', description: 'a' } },
      node_config: {
        programming_language: 'python',
        process_fn:
          'def process_fn(inputs):\n' +
          '    import time\n' +
          `    time.sleep(${sleepSecs})\n` +
          '    return {"out_a": "A-done"}',
      },
      children: ['node_5'],
      __attributes__: { x: 480, y: 0 },
    },
    {
      node_id: 'node_4',
      node_name: 'BranchB',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: {},
      output_fields: { out_b: { type: 'string', description: 'b' } },
      node_config: {
        programming_language: 'python',
        // Writes a /run file so the WORKFLOW_SANDBOX Explorer has something to
        // show after the run (run_dir retained for debug).
        process_fn:
          'def process_fn(inputs):\n' +
          '    import time\n' +
          `    time.sleep(${sleepSecs})\n` +
          '    with open("/run/branch_b.txt", "w") as f:\n' +
          '        f.write("hello from branch B")\n' +
          '    return {"out_b": "B-done"}',
      },
      children: ['node_5'],
      __attributes__: { x: 480, y: 240 },
    },
    {
      node_id: 'node_5',
      node_name: 'merge',
      node_type: 'ParallelEndNode',
      node_description: 'join',
      input_fields: {},
      output_fields: {},
      node_config: { parallel_start_node_id: 'node_2' },
      children: ['node_6'],
      __attributes__: { x: 720, y: 120 },
    },
    {
      node_id: 'node_6',
      node_name: '__end__',
      node_type: 'EndNode',
      node_description: '',
      input_fields: {
        out_a: { type: 'string', value: '', reference: 'BranchA.out_a' },
        out_b: { type: 'string', value: '', reference: 'BranchB.out_b' },
      },
      output_fields: {
        out_a: { type: 'string', description: 'a' },
        out_b: { type: 'string', description: 'b' },
      },
      node_config: {},
      children: [],
      __attributes__: { x: 960, y: 120 },
    },
  ];
}

test('K1 parallel workflow → multiple nodes breathe simultaneously, then complete', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Parallel');
  await seedNodes(user.token, wfId, parallelSlowNodes(3));
  await open(page, wfId);

  // Toolbar Execute opens the workflow Run tab; Run (no inputs) starts the SSE.
  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible({ timeout: 10_000 });
  await page.locator('[data-action="run-workflow"]').click();

  // Both parallel branch nodes should enter the `running` breathing state at
  // once. We assert on the canvas card's data-exec-state (set by CustomNode),
  // independent of which inspector tab is showing.
  const branchA = page.locator('[aria-label="CodeNode BranchA"]');
  const branchB = page.locator('[aria-label="CodeNode BranchB"]');

  await expect(branchA).toHaveAttribute('data-exec-state', 'running', {
    timeout: 20_000,
  });
  await expect(branchB).toHaveAttribute('data-exec-state', 'running', {
    timeout: 20_000,
  });
  // Both carry the calm breathing-halo animation class simultaneously.
  await expect(branchA).toHaveClass(/animate-node-breathe/);
  await expect(branchB).toHaveClass(/animate-node-breathe/);
  // THE headline shot: two nodes breathing at once.
  await shot(page, '01-parallel-breathing');

  // Then they turn green (completed) and the run reaches terminal completed.
  await expect(branchA).toHaveAttribute('data-exec-state', 'completed', {
    timeout: 30_000,
  });
  await expect(branchB).toHaveAttribute('data-exec-state', 'completed', {
    timeout: 30_000,
  });
  // The Run tab's inline output region shows the terminal status.
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 30_000,
  });
  await shot(page, '02-parallel-completed');
});

test('K2 post-run debug — execution tab outputs, run-folder files, reload-safe', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Debug');
  await seedNodes(user.token, wfId, parallelSlowNodes(1));
  await open(page, wfId);

  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible({ timeout: 10_000 });
  await page.locator('[data-action="run-workflow"]').click();
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 40_000,
  });

  // Every node shows a per-node card; the cards are keyed by node_id
  // (BranchA = node_3, BranchB = node_4 in parallelSlowNodes).
  await expect(page.getByTestId('exec-node-card').first()).toBeVisible();
  const branchACard = page.locator(
    '[data-testid="exec-node-card"][data-node-id="node_3"]',
  );
  await expect(branchACard).toBeVisible();
  await expect(branchACard.getByTestId('exec-node-status')).toHaveText(
    /completed/,
  );
  await shot(page, '03-execution-tab-outputs');

  // Open the Explorer → the Sandbox block lists the /run file branch B wrote.
  // The run root is deliberately expanded by default for immediate debugging.
  await page.locator('[data-action="files"]').click();
  await expect(page.getByRole('button', { name: 'Sandbox', exact: true })).toBeVisible({
    timeout: 10_000,
  });
  const runFolder = page.getByRole('button', { name: /^run$/ });
  await expect(runFolder).toBeVisible({ timeout: 15_000 });
  await expect(runFolder).toHaveAttribute('aria-expanded', 'true');
  const runFile = page.getByRole('button', { name: /branch_b\.txt/i });
  await expect(runFile).toBeVisible({ timeout: 10_000 });
  await shot(page, '04-workflow-sandbox-runfiles');

  // Open the run file → its content modal shows.
  await runFile.dblclick();
  await expect(page.getByText(/hello from branch B/)).toBeVisible({
    timeout: 10_000,
  });
  await shot(page, '05-runfile-content');

  // Reload → the in-memory store is gone, but the per-node status must rehydrate
  // from the persisted execution record (GET /executions/{id}).
  await page.reload();
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  // Nothing selected → workflow scope; open the Run tab whose inline output
  // region rehydrates the per-node status from the persisted execution record.
  await page.getByTestId('inspector-tab-run').click();
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 20_000,
  });
  await expect(
    page.locator('[data-testid="exec-node-card"][data-node-id="node_3"]'),
  ).toBeVisible({ timeout: 15_000 });
  await shot(page, '06-reload-persisted');
});

test('K3 node Execute panel → run a node in isolation → output log', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Node Panel');
  await seedNodes(user.token, wfId, [
    {
      node_id: 'node_1',
      node_name: '__start__',
      node_type: 'StartNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: {},
      children: ['node_2'],
      __attributes__: { x: 0, y: 0 },
    },
    {
      node_id: 'node_2',
      node_name: 'Echo',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: { name: { type: 'string', value: '', reference: '' } },
      output_fields: { result: { type: 'string', description: 'r' } },
      node_config: {
        programming_language: 'python',
        process_fn:
          'def process_fn(inputs):\n    return {"result": "hi-" + str(inputs.get("name"))}',
      },
      children: ['node_3'],
      __attributes__: { x: 250, y: 0 },
    },
    {
      node_id: 'node_3',
      node_name: '__end__',
      node_type: 'EndNode',
      node_description: '',
      input_fields: { result: { type: 'string', value: '', reference: 'Echo.result' } },
      output_fields: { result: { type: 'string', description: 'r' } },
      node_config: {},
      children: [],
      __attributes__: { x: 500, y: 0 },
    },
  ]);
  await open(page, wfId);

  // Select the CodeNode → open the node-scope "Run node" tab (renamed).
  await page.locator('[aria-label="CodeNode Echo"]').click();
  await page.getByTestId('inspector-tab-run-node').click();
  await expect(page.getByTestId('node-execute-panel')).toBeVisible();

  // Fill the input (commit-on-blur) + run.
  await page.getByTestId('node-exec-name-input').fill('panel');
  await page.getByTestId('node-exec-name-input').blur();
  await page.getByTestId('node-exec-run').click();

  // Output log surfaces the result.
  await expect(page.getByTestId('node-exec-status')).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId('node-exec-result')).toContainText(/hi-panel/, {
    timeout: 20_000,
  });
  await shot(page, '07-node-execute-panel');
});

test('K4 batch → CSV → submit → View task hand-off', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Batch');
  await seedNodes(user.token, wfId, [
    {
      node_id: 'node_1',
      node_name: '__start__',
      node_type: 'StartNode',
      node_description: '',
      input_fields: { name: { type: 'string', value: '', reference: '' } },
      output_fields: { name: { type: 'string', description: 'n' } },
      node_config: {},
      children: ['node_2'],
      __attributes__: { x: 0, y: 0 },
    },
    {
      node_id: 'node_2',
      node_name: 'Echo',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: { name: { type: 'string', value: '', reference: '__start__.name' } },
      output_fields: { result: { type: 'string', description: 'r' } },
      node_config: {
        programming_language: 'python',
        process_fn: 'def process_fn(inputs):\n    return {"result": str(inputs.get("name"))}',
      },
      children: ['node_3'],
      __attributes__: { x: 250, y: 0 },
    },
    {
      node_id: 'node_3',
      node_name: '__end__',
      node_type: 'EndNode',
      node_description: '',
      input_fields: { result: { type: 'string', value: '', reference: 'Echo.result' } },
      output_fields: { result: { type: 'string', description: 'r' } },
      node_config: {},
      children: [],
      __attributes__: { x: 500, y: 0 },
    },
  ]);
  await open(page, wfId);

  // Run Batch opens the inspector Batch tab (modal retired).
  await page.locator('[data-action="canvas-run-batch"]').click();
  await expect(page.getByTestId('batch-tab')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('batch-source-selector')).toBeVisible();
  await page.getByTestId('batch-csv-input').setInputFiles({
    name: 'rows.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('name\nalice\nbob\n'),
  });
  await expect(page.getByTestId('mapping-name')).toContainText('name', {
    timeout: 10_000,
  });
  await page.getByTestId('batch-submit').click();

  // The hand-off: a "View task" affordance → the task-center progress view.
  const viewTask = page.getByText(/view task/i);
  await expect(viewTask).toBeVisible({ timeout: 15_000 });
  await shot(page, '08-batch-submitted');

  await viewTask.click();
  await expect(page).toHaveURL(/\/tasks\//, { timeout: 15_000 });
  await shot(page, '09-batch-task-progress');
});

test('K5 cancel — slow workflow → Execute → Cancel → cancelled', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Cancel');
  // A single slow CodeNode so the run is unambiguously in-flight when we cancel.
  await seedNodes(user.token, wfId, [
    {
      node_id: 'node_1',
      node_name: '__start__',
      node_type: 'StartNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: {},
      children: ['node_2'],
      __attributes__: { x: 0, y: 0 },
    },
    {
      node_id: 'node_2',
      node_name: 'Slow',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: {},
      output_fields: { result: { type: 'string', description: 'r' } },
      node_config: {
        programming_language: 'python',
        process_fn: 'def process_fn(inputs):\n    import time\n    time.sleep(20)\n    return {"result": "done"}',
      },
      children: ['node_3'],
      __attributes__: { x: 250, y: 0 },
    },
    {
      node_id: 'node_3',
      node_name: '__end__',
      node_type: 'EndNode',
      node_description: '',
      input_fields: { result: { type: 'string', value: '', reference: 'Slow.result' } },
      output_fields: { result: { type: 'string', description: 'r' } },
      node_config: {},
      children: [],
      __attributes__: { x: 500, y: 0 },
    },
  ]);
  await open(page, wfId);

  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible({ timeout: 10_000 });
  await page.locator('[data-action="run-workflow"]').click();
  // The sticky Run action swaps Execute → Cancel while running.
  const runAction = page.locator('[data-action="run-workflow"]');
  await expect(runAction).toHaveText(/cancel/i, {
    timeout: 15_000,
  });
  // The Run tab's inline output region shows the running status.
  await expect(page.getByTestId('exec-status')).toHaveText(/running/, {
    timeout: 15_000,
  });

  await runAction.click();
  await expect(page.getByTestId('exec-status')).toHaveText(/cancelled/, {
    timeout: 20_000,
  });
  await shot(page, '10-cancelled');
});
