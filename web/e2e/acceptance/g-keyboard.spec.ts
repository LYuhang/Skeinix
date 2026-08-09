/**
 * Acceptance G — Keyboard / clipboard: Cmd+C/V/D duplicate, Delete removes,
 * guard while typing in a field.
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
const MOD = process.platform === 'darwin' ? 'Meta' : 'Control';

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
  // New workflows are empty by design; seed a single StartNode as the starting
  // point these keyboard cases assume before adding more nodes.
  await seedNodes(user.token, wfId, [startNodeOnly()]);
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
}

// Add a node via the LEFT Explorer node palette (the right-click "Add node…"
// item was removed; node-adding moved to the palette). Open the Explorer
// ("Files"), expand the "Nodes" block (collapsed by default), then
// DOUBLE-CLICK the card → inserts at viewport center.
async function addNode(page: Page, nodeType: string): Promise<void> {
  const before = await page.locator('.react-flow__node').count();
  if (!(await page.locator('[data-block-id="nodes"]').count())) {
    await page.locator('[data-action="files"]').click();
  }
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

test('G1 Cmd/Ctrl+D duplicates the selected node', async ({ page }) => {
  await openCanvas(page, 'Acc KB Duplicate');
  await addNode(page, 'CodeNode');
  await page.locator('[aria-label^="CodeNode"]').first().click();
  await expect(page.locator('.react-flow__node')).toHaveCount(2);

  await page.keyboard.press(`${MOD}+d`);
  // Start + 2 CodeNodes.
  await expect(page.locator('.react-flow__node')).toHaveCount(3);
  await expect(page.locator('[aria-label^="CodeNode"]')).toHaveCount(2);
  await screenshot(page, '70-keyboard-duplicate');
});

test('G2 Cmd+C / Cmd+V copy-paste a node', async ({ page }) => {
  await openCanvas(page, 'Acc KB CopyPaste');
  await addNode(page, 'PromptNode');
  await page.locator('[aria-label^="PromptNode"]').first().click();
  await page.keyboard.press(`${MOD}+c`);
  await page.keyboard.press(`${MOD}+v`);
  await expect(page.locator('[aria-label^="PromptNode"]')).toHaveCount(2);
});

test('G3 Delete key removes the selected node', async ({ page }) => {
  await openCanvas(page, 'Acc KB Delete');
  await addNode(page, 'CodeNode');
  await page.locator('[aria-label^="CodeNode"]').first().click();
  await expect(page.locator('.react-flow__node')).toHaveCount(2);
  await page.keyboard.press('Delete');
  await expect(page.locator('.react-flow__node')).toHaveCount(1);
});

test('G4 Delete is a no-op while typing in a field', async ({ page }) => {
  await openCanvas(page, 'Acc KB Guard');
  await addNode(page, 'CodeNode');
  await page.locator('[aria-label^="CodeNode"]').first().click();

  // Focus the node-name input and type, then press Backspace — it must edit
  // text, NOT delete the node.
  const nameInput = page.locator('input#node-name-node_2');
  await nameInput.click();
  await nameInput.fill('abc');
  await page.keyboard.press('Backspace');
  // The node still exists (Backspace edited text, not the canvas).
  await expect(page.locator('.react-flow__node')).toHaveCount(2);
  await expect(nameInput).toHaveValue('ab');
  await screenshot(page, '71-keyboard-guard');
});
