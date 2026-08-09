/**
 * Acceptance J — Run Batch: source selector + CSV upload + mapping + submit
 * The batch modal is folded into the Inspector Batch tab
 * (testid `batch-tab`); the stable submit/source/mapping testids are preserved.
 */
import { test, expect } from '@playwright/test';
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

test('G1 Run Batch → source selector → CSV → mapping → submit → task', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Acc Batch Flow');
  // StartNode declares an input `name` so the column-mapping UI renders.
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

  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });

  // Run Batch opens the inspector Batch tab (modal retired).
  await page.locator('[data-action="canvas-run-batch"]').click();
  await expect(page.getByTestId('batch-tab')).toBeVisible({ timeout: 10_000 });
  // Source selector present (upload / inline data).
  await expect(page.getByTestId('batch-source-selector')).toBeVisible();
  await expect(page.getByTestId('batch-source-data')).toBeVisible();

  // Upload a CSV with a matching `name` column → mapping auto-maps.
  await page.getByTestId('batch-csv-input').setInputFiles({
    name: 'rows.csv',
    mimeType: 'text/csv',
    buffer: Buffer.from('name\nalice\nbob\n'),
  });
  await expect(page.getByTestId('mapping-name')).toBeVisible({ timeout: 10_000 });
  // Auto-mapped by column name.
  await expect(page.getByTestId('mapping-name')).toContainText('name');
  await screenshot(page, '60-batch-source-mapping');

  // Submit → success toast with a "View task" action.
  await page.getByTestId('batch-submit').click();
  await expect(page.getByText(/view task/i)).toBeVisible({ timeout: 15_000 });
  await screenshot(page, '60-batch-submit');
});
