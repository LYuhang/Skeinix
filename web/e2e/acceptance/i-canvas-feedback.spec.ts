/**
 * Acceptance I — Canvas feedback: per-node ⚠ badge, dashed pairing edge for
 * a paired parallel branch.
 */
import { test, expect, type Page } from '@playwright/test';
import {
  registerRealUser,
  seedAuth,
  createWorkflow,
  seedNodes,
  screenshot,
  type RealUser,
} from './fixtures';

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

test('I1 incomplete node shows a ⚠ warning badge', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Warn Badge');
  // A ParallelStart with no parallel_end_node_id is an unpaired/incomplete
  // node → nodeWarnings() flags it → ⚠ badge on the card.
  await seedNodes(user.token, wfId, [
    {
      node_id: 'node_1',
      node_name: 'Start',
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
      node_name: 'Fork',
      node_type: 'ParallelStartNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: { branches: {}, parallel_end_node_id: '' },
      children: [],
      __attributes__: { x: 250, y: 0 },
    },
  ]);
  await open(page, wfId);
  await expect(page.locator('[data-node-warning]').first()).toBeVisible({ timeout: 10_000 });
  await screenshot(page, '90-node-warning-badge');
});

test('I2 a paired Loop pair renders a dashed loop-back edge', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Loop Pairing Edge');
  await seedNodes(user.token, wfId, [
    {
      node_id: 'node_1',
      node_name: 'Start',
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
      node_name: 'Loop begin',
      node_type: 'LoopBeginNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: { loop_end_node_id: 'node_3' },
      children: [],
      __attributes__: { x: 250, y: 0 },
    },
    {
      node_id: 'node_3',
      node_name: 'Loop end',
      node_type: 'LoopEndNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: { loop_begin_node_id: 'node_2' },
      children: [],
      __attributes__: { x: 500, y: 0 },
    },
  ]);
  await open(page, wfId);
  // The config-derived pairing edge has id `pair:node_2->node_3`.
  await expect(page.locator('.react-flow__edge[data-id="pair:node_2->node_3"]')).toBeVisible({
    timeout: 10_000,
  });
  await screenshot(page, '91-pairing-edge');
});

test('I3 empty-state overlay shows when no nodes', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Empty State');
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('.react-flow__node')).toHaveCount(0);
  await expect(page.locator('[data-canvas-empty-state]')).toBeVisible();
  await screenshot(page, '92-empty-state');
});
