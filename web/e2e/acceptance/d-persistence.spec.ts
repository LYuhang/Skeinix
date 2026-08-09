/**
 * Acceptance D — Persistence + history: save, undo/redo (+Cmd+Z),
 * exit/re-enter rendering and the unsaved-changes guard.
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

async function openCanvas(page: Page, name: string): Promise<string> {
  const wfId = await createWorkflow(user.token, name);
  // New workflows are empty by design; seed a single StartNode as the starting
  // point these persistence cases assume before adding more nodes.
  await seedNodes(user.token, wfId, [startNodeOnly()]);
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
  return wfId;
}

// Add a node via the LEFT Explorer node palette. The right-click "Add node…"
// context-menu item was intentionally removed (node-adding moved to the
// palette). Open the Explorer (CanvasToolbar "Files"), expand the "Nodes"
// block (collapsed by default), then DOUBLE-CLICK the node card — which
// inserts that node at the viewport center (NodeCard.onDoubleClick → addNode).
async function addNode(page: Page, nodeType: string): Promise<void> {
  const before = await page.locator('.react-flow__node').count();
  // Open the Explorer rail if it isn't already open.
  if (!(await page.locator('[data-block-id="nodes"]').count())) {
    await page.locator('[data-action="files"]').click();
  }
  // Expand the "Nodes" palette block if collapsed.
  const block = page.locator('[data-block-id="nodes"]');
  await block.waitFor({ state: 'visible' });
  const header = block.locator('button[aria-expanded]').first();
  if ((await header.getAttribute('aria-expanded')) === 'false') {
    await header.click();
  }
  const card = block.locator(`[data-node-card][data-node-type="${nodeType}"]`);
  await card.waitFor({ state: 'visible' });
  await card.dblclick();
  await expect(page.locator('.react-flow__node')).toHaveCount(before + 1, {
    timeout: 10_000,
  });
}

test('D1 Save: dirty → clean (Save disables, toast)', async ({ page }) => {
  await openCanvas(page, 'Acc Save Flow');
  await addNode(page, 'CodeNode');
  // Adding a node dirties → Save enabled.
  await expect(page.locator('[data-action="canvas-save"]')).toBeEnabled();
  await page.locator('[data-action="canvas-save"]').click();
  await expect(page.getByText('Saved')).toBeVisible({ timeout: 10_000 });
  // Clean again → Save disabled.
  await expect(page.locator('[data-action="canvas-save"]')).toBeDisabled();
  await screenshot(page, '30-save');
});

test('D2 Undo/Redo (button + Cmd/Ctrl+Z)', async ({ page }) => {
  await openCanvas(page, 'Acc Undo Flow');
  await addNode(page, 'CodeNode');
  await expect(page.locator('.react-flow__node')).toHaveCount(2);

  // Undo button removes the added node.
  await page.locator('[data-action="undo"]').click();
  await expect(page.locator('.react-flow__node')).toHaveCount(1);

  // Redo button re-adds it.
  await page.locator('[data-action="redo"]').click();
  await expect(page.locator('.react-flow__node')).toHaveCount(2);

  // Keyboard undo (Cmd/Ctrl+Z). Click the pane first so focus is on canvas.
  // With the Explorer palette open the canvas pane is a narrow middle column;
  // click near its top-left (inside its real box) so the click lands on the
  // pane background and isn't clamped onto the overlapping Inspector.
  await page.locator('[data-canvas-pane]').click({ position: { x: 20, y: 20 } });
  const mod = process.platform === 'darwin' ? 'Meta' : 'Control';
  await page.keyboard.press(`${mod}+z`);
  await expect(page.locator('.react-flow__node')).toHaveCount(1);
  await screenshot(page, '31-undo-redo');
});

test('D3 exit → reenter re-renders persisted graph', async ({ page }) => {
  const wfId = await openCanvas(page, 'Acc Reenter Flow');
  await addNode(page, 'CodeNode');
  await addNode(page, 'EndNode');
  await page.locator('[data-action="canvas-save"]').click();
  await expect(page.getByText('Saved')).toBeVisible({ timeout: 10_000 });
  await expect(page.locator('.react-flow__node')).toHaveCount(3);

  // Navigate away (no unsaved guard since clean) and back.
  await page.getByTestId('header-back').click();
  await page.waitForURL(/\/workspace$/);
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.locator('.react-flow__node')).toHaveCount(3);
  await screenshot(page, '32-exit-reenter');
});

test('D5 unsaved-changes guard prompts on navigate-away when dirty', async ({
  page,
}) => {
  await openCanvas(page, 'Acc Guard Flow');
  await addNode(page, 'CodeNode'); // dirty, not saved
  await page.getByTestId('header-back').click();
  // The unsaved-changes dialog blocks navigation.
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText(/unsaved|save|discard/i).first()).toBeVisible();
  await screenshot(page, '34-unsaved-guard');
});
