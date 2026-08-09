/**
 * Validation through the authoritative backend Check action.
 */
import { test, expect, type Page } from '@playwright/test';
import {
  registerRealUser,
  seedAuth,
  createWorkflow,
  seedNodes,
  codeOnlyWorkflowNodes,
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

test('E1 Check on a valid graph → valid', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Check OK');
  await seedNodes(user.token, wfId, codeOnlyWorkflowNodes());
  await open(page, wfId);

  // Check moved into the ⋯ (More) menu in the Inspector redesign (#481) —
  // open the menu before clicking the item.
  await page.locator('[data-action="canvas-more"]').click();
  await page.locator('[data-action="check"]').click();
  await expect(page.locator('[data-role="check-ok"]')).toBeVisible({ timeout: 15_000 });
  await screenshot(page, '40-check-ok');
});

test('E2 Check on an invalid graph → error message', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Check Fail');
  // Unpaired ParallelStart (no ParallelEnd) + a dangling next_node_id —
  // the authoritative backend Check must reject this.
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

  // Check moved into the ⋯ (More) menu in the Inspector redesign (#481) —
  // open the menu before clicking the item.
  await page.locator('[data-action="canvas-more"]').click();
  await page.locator('[data-action="check"]').click();
  await expect(page.locator('[data-role="check-fail"]')).toBeVisible({ timeout: 15_000 });
  // The error message is non-empty.
  await expect(page.locator('[data-role="check-fail"]')).not.toHaveText('');
  await screenshot(page, '41-check-fail');
});
