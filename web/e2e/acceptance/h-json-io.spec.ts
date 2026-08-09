/**
 * Acceptance H — JSON IO: Download (.json) + Upload (strip meta, loads onto
 * canvas, including confirmation over a non-empty canvas.
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

/** A single StartNode (no children) shaped like codeOnlyWorkflowNodes()[0]. */
function startNodeOnly(): Record<string, unknown> {
  return {
    node_id: 'node_1',
    node_name: '__start__',
    node_type: 'StartNode',
    node_description: '',
    input_fields: {},
    output_fields: { user_query: { type: 'string', description: 'user input' } },
    node_config: {},
    children: [],
    __attributes__: { x: 0, y: 0 },
  };
}

test.beforeAll(async () => {
  user = await registerRealUser();
});

test.beforeEach(async ({ context }) => {
  await seedAuth(context, user.token);
});

async function openCanvas(page: Page, name: string): Promise<void> {
  const wfId = await createWorkflow(user.token, name);
  // New workflows are empty by design; seed a single StartNode so the canvas is
  // non-empty (H2's upload routes through the confirm dialog only when so).
  await seedNodes(user.token, wfId, [startNodeOnly()]);
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
}

test('H1 Download serializes the draft to a .json file', async ({ page }) => {
  await openCanvas(page, 'Acc JSON Download');
  await page.locator('[data-action="canvas-more"]').click();
  const downloadPromise = page.waitForEvent('download');
  await page.locator('[data-action="wf-download"]').click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.json$/);
  await screenshot(page, '80-json-download');
});

test('H2 Upload loads nodes onto a (non-empty) canvas via confirm dialog', async ({
  page,
}) => {
  await openCanvas(page, 'Acc JSON Upload');
  // Canvas already has the seeded StartNode → upload routes through confirm.
  const uploadJson = JSON.stringify({
    __meta__: { workflow_name: 'discarded identity' },
    node_1: {
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
    node_2: {
      node_id: 'node_2',
      node_name: 'UploadedCode',
      node_type: 'CodeNode',
      node_description: '',
      input_fields: {},
      output_fields: {},
      node_config: { programming_language: 'python', process_fn: 'def process_fn(inputs):\n    return {}' },
      children: [],
      __attributes__: { x: 250, y: 0 },
    },
  });

  await page.locator('[data-action="canvas-more"]').click();
  // The hidden file input is set directly (the menu item just clicks it).
  await page.getByTestId('wf-upload-input').setInputFiles({
    name: 'upload.json',
    mimeType: 'application/json',
    buffer: Buffer.from(uploadJson),
  });
  // Confirm dialog (canvas non-empty) → Replace.
  await expect(page.getByTestId('upload-confirm-dialog')).toBeVisible({ timeout: 10_000 });
  await page.locator('[data-action="wf-upload-confirm"]').click();

  // The uploaded CodeNode is now on the canvas.
  await expect(page.locator('.react-flow__node').filter({ hasText: 'UploadedCode' })).toBeVisible({
    timeout: 10_000,
  });
  await screenshot(page, '81-json-upload');
});
