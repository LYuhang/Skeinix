/**
 * Acceptance F — Execution: single/debug streaming + cancel + the inline
 * workflow Run tab.
 *
 * The Execute/Batch modals are retired: the toolbar Execute button now opens
 * the Inspector's workflow `Run` tab (testid `inspector-tab-run` /
 * `workflow-run-tab`), where the StartNode input form + the Run trigger
 * (`[data-action="run-workflow"]`) + the per-node output cards all live inline.
 *
 * Uses a CodeNode-only workflow for determinism (no LLM key in dev).
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

/** Toolbar Execute → the inspector workflow Run tab is focused. */
async function openRunTab(page: Page): Promise<void> {
  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible({ timeout: 10_000 });
}

test('F1 execute a no-input CodeNode workflow → streams per-node status', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Success');
  await seedNodes(user.token, wfId, codeOnlyWorkflowNodes());
  await open(page, wfId);

  await openRunTab(page);
  // No inputs declared → Run immediately.
  await page.locator('[data-action="run-workflow"]').click();

  // The output region renders inline in the Run tab.
  await expect(page.getByTestId('run-output')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('exec-node-card').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 30_000,
  });
  await screenshot(page, '50-execute-success');
});

test('F2 a failing node → execution surfaces an error', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Error');
  const nodes = codeOnlyWorkflowNodes();
  // Make the CodeNode raise.
  (nodes[1].node_config as Record<string, unknown>).process_fn =
    'def process_fn(inputs):\n    raise ValueError("boom")';
  await seedNodes(user.token, wfId, nodes);
  await open(page, wfId);

  await openRunTab(page);
  await page.locator('[data-action="run-workflow"]').click();

  // The executions route catches a raising node into the terminal frame's
  // `errors[node_id]` map while the OVERALL run still reports `completed`. The
  // node error surfaces on the per-node card (status=error + message).
  const errorCard = page.getByTestId('exec-node-card').filter({
    has: page.getByTestId('exec-node-status').filter({ hasText: 'error' }),
  });
  await expect(errorCard.first()).toBeVisible({ timeout: 30_000 });
  await expect(errorCard.first()).toContainText(/boom/);
  await screenshot(page, '51-execute-error');
});

test('F3 inline input form when StartNode declares inputs', async ({ page }) => {
  const wfId = await createWorkflow(user.token, 'Acc Exec Input');
  const nodes = codeOnlyWorkflowNodes();
  // StartNode declares an input → the Run tab renders an input form.
  (nodes[0] as Record<string, unknown>).input_fields = {
    name: { type: 'string', value: '', reference: '' },
  };
  // Code echoes the input so the result is deterministic.
  (nodes[1] as Record<string, unknown>).input_fields = {
    name: { type: 'string', value: '', reference: '__start__.name' },
  };
  (nodes[1].node_config as Record<string, unknown>).process_fn =
    'def process_fn(inputs):\n    return {"result": "hi-" + str(inputs.get("name"))}';
  (nodes[0] as Record<string, unknown>).output_fields = {
    name: { type: 'string', description: 'name in' },
  };
  await seedNodes(user.token, wfId, nodes);
  await open(page, wfId);

  await openRunTab(page);
  // The input form appears inline (no modal); fill it + Run.
  await expect(page.getByTestId('exec-field-name')).toBeVisible({ timeout: 10_000 });
  await screenshot(page, '53-execute-input-form');

  await page
    .getByTestId('exec-field-name')
    .locator('input, textarea')
    .first()
    .fill('world');
  await page.locator('[data-action="run-workflow"]').click();

  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 30_000,
  });
  await screenshot(page, '53-execute-input-result');
});
