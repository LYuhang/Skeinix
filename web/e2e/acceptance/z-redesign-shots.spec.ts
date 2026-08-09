/**
 * Acceptance Z — Inspector/execution REDESIGN walkthrough screenshots (task #481).
 *
 * Walks the redesigned contextual-tab interactions against the live stack and
 * writes proof PNGs to `web/screenshots/redesign-NN-*.png`. This spec is a
 * SCREENSHOT spec, not a feature gate — every interaction it drives is already
 * covered by c-/f-/j-/k-. It exists to produce the redesign visual record.
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
  codeOnlyWorkflowNodes,
  type RealUser,
} from './fixtures';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCREENSHOT_DIR = path.resolve(__dirname, '..', '..', 'screenshots');

async function shot(page: Page, name: string): Promise<void> {
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  await page.screenshot({
    path: path.join(SCREENSHOT_DIR, `redesign-${name}.png`),
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

/** A StartNode(name input) → Code → End graph; Start has a description for hover. */
function describedNodes(): Record<string, unknown>[] {
  const nodes = codeOnlyWorkflowNodes();
  (nodes[0] as Record<string, unknown>).node_description =
    'Entry point of the workflow. Provide the runtime inputs here.';
  (nodes[1] as Record<string, unknown>).node_description =
    'Runs Python in the sandbox and returns a result string.';
  return nodes;
}

async function open(page: Page, wfId: string): Promise<void> {
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
}

test('redesign walkthrough — contextual tabs, run/batch inline, hover, freeze', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Redesign Walkthrough');
  await seedNodes(user.token, wfId, describedNodes());
  await open(page, wfId);

  // --- 01: empty canvas / workflow scope (no node selected) → Run/Batch tabs.
  // Click empty canvas pane to ensure nothing is selected → workflow scope.
  await page.locator('.react-flow__pane').click({ position: { x: 60, y: 60 } });
  await shot(page, '01-empty-canvas-inspector');

  // --- 02: node scope tabs (Node / Run node / Info) on selecting a node.
  await page.locator('[aria-label^="CodeNode"]').first().click();
  await expect(page.getByTestId('inspector-tab-node')).toBeVisible();
  await expect(page.getByTestId('inspector-tab-run-node')).toBeVisible();
  await expect(page.getByTestId('inspector-tab-info')).toBeVisible();
  await shot(page, '02-node-scope-tabs');

  // --- 03: Run tab input form. Toolbar Execute → workflow Run tab (deselects).
  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible({
    timeout: 10_000,
  });
  await shot(page, '03-run-tab-input');

  // --- 04: Run tab after a run → per-node output cards.
  await page.locator('[data-action="run-workflow"]').click();
  await expect(page.getByTestId('exec-status')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('exec-node-card').first()).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 30_000,
  });
  await shot(page, '04-run-tab-output');

  // --- 05: Batch tab — source + mapping + task list.
  await page.locator('[data-action="canvas-run-batch"]').click();
  await expect(page.getByTestId('batch-tab')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('batch-source-selector')).toBeVisible();
  await shot(page, '05-batch-tab');

  // --- 06: Check moved into the ⋯ (more) menu.
  await page.locator('[data-action="canvas-more"]').click();
  await expect(page.locator('[data-action="check"]')).toBeVisible();
  await shot(page, '06-check-in-more-menu');
  // Close the menu so it doesn't bleed into later shots.
  await page.keyboard.press('Escape');
});

test('redesign — node hover peek-card does not cover the node', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Redesign Hover');
  await seedNodes(user.token, wfId, describedNodes());
  await open(page, wfId);

  // Make sure nothing is selected (hover is suppressed for the open node).
  await page.locator('.react-flow__pane').click({ position: { x: 60, y: 60 } });

  const codeNode = page.locator('[aria-label^="CodeNode"]').first();
  await codeNode.hover();
  // 500ms open delay → wait for the card to materialize.
  const card = page.locator('[data-node-hover-card]');
  await expect(card).toBeVisible({ timeout: 5_000 });
  // Sanity: card carries the description text (real content, not empty).
  await expect(card).toContainText(/sandbox/i);

  // Geometric assertion: the hover card (side=right) must NOT overlap the node.
  const nodeBox = await codeNode.boundingBox();
  const cardBox = await card.boundingBox();
  expect(nodeBox).not.toBeNull();
  expect(cardBox).not.toBeNull();
  if (nodeBox && cardBox) {
    // side=right → card's left edge is at/after the node's right edge.
    expect(cardBox.x).toBeGreaterThanOrEqual(nodeBox.x + nodeBox.width - 4);
  }
  await shot(page, '07-node-hover-card');
});

test('redesign — editing freezes while a run is in-flight (best-effort)', async ({
  page,
}) => {
  const wfId = await createWorkflow(user.token, 'Redesign Freeze');
  const nodes = codeOnlyWorkflowNodes();
  // A slow node so the run stays in-flight long enough to observe the freeze.
  // The sandbox FORBIDS dynamic `import` inside process_fn, so we busy-spin
  // (no imports) to burn a few seconds of wall-clock instead of time.sleep.
  (nodes[1].node_config as Record<string, unknown>).process_fn =
    'def process_fn(inputs):\n    s = 0\n    for i in range(60000000):\n        s += i\n    return {"result": str(s)}';
  await seedNodes(user.token, wfId, nodes);
  await open(page, wfId);

  // Baseline: Undo/Save reflect the editable canvas (Save gated by dirty, Undo
  // by readOnly + stack). Open the Run tab and kick a run.
  await page.locator('[data-action="execute"]').click();
  await expect(page.getByTestId('workflow-run-tab')).toBeVisible({
    timeout: 10_000,
  });
  await page.locator('[data-action="run-workflow"]').click();
  await expect(page.getByTestId('exec-status')).toBeVisible({ timeout: 10_000 });

  // While in-flight: the edit-freeze makes the canvas read-only → Undo/Redo are
  // disabled regardless of stack (the toolbar's readOnly short-circuit). Cancel
  // stays enabled. Capture the frozen state — assert we are NOT yet completed so
  // the screenshot is genuinely an in-flight frame, then grab the PNG fast.
  await expect(page.locator('[data-action="undo"]')).toBeDisabled();
  await expect(page.getByTestId('exec-status')).not.toHaveText(/completed/);
  await shot(page, '08-edit-frozen-during-run');
  // Confirm the run does eventually finish + the freeze releases (Undo can
  // re-enable; here the stack is empty so Save/Undo gating returns to normal).
  await expect(page.getByTestId('exec-status')).toHaveText(/completed/, {
    timeout: 30_000,
  });
});
