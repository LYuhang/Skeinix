/**
 * Acceptance B — Build: add node types, connect via handle-drag, delete
 * node and edge deletion with persistence across save and reload.
 *
 * The newly-built manual editor (HEAD 4561ac4) wires `onConnect` (handle
 * drag → `connectNodes`) and `context-delete-edge` → `disconnectNodes`
 * (persisted to `children[]`), which this spec exercises end-to-end.
 */
import { test, expect, type Page } from '@playwright/test';
import { registerRealUser, seedAuth, createWorkflow, seedNodes, screenshot, type RealUser } from './fixtures';

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

/**
 * A disconnected CodeNode placed WELL CLEAR of the StartNode, for the
 * handle-drag (connect/disconnect) cases B2/B3. Palette inserts always land at
 * the viewport center (can stack + overlap handles), which makes a reliable
 * handle-to-handle drag impractical; the spec explicitly allows seeding the two
 * nodes at known positions for B2/B3 since their intent is CONNECT/disconnect,
 * not add-via-UI. The Start sits at (0,0); this CodeNode is far below-right so
 * the Start's source-handle and the Code's target-handle never overlap.
 */
function disconnectedCodeNode(): Record<string, unknown> {
  return {
    node_id: 'node_2',
    node_name: 'Compute',
    node_type: 'CodeNode',
    node_description: '',
    input_fields: {},
    output_fields: { result: { type: 'string', description: 'computed' } },
    node_config: {
      programming_language: 'python',
      process_fn: 'def process_fn(inputs):\n    return {"result": "x"}',
    },
    children: [],
    __attributes__: { x: 120, y: 280 },
  };
}

test.beforeAll(async () => {
  user = await registerRealUser();
});

test.beforeEach(async ({ context }) => {
  await seedAuth(context, user.token);
  // Kill CSS animations/transitions so Radix menu/dialog enter-animations
  // don't trip Playwright's element-stability check on the add-node flow.
  await context.addInitScript(() => {
    const style = document.createElement('style');
    style.innerHTML =
      '*,*::before,*::after{animation-duration:0s!important;animation-delay:0s!important;transition-duration:0s!important;transition-delay:0s!important;}';
    document.documentElement.appendChild(style);
  });
});

/**
 * Open a fresh workflow's canvas and wait for the seeded StartNode. Seeds a
 * single StartNode by default; callers (B2/B3) may pass extra nodes to plant a
 * known multi-node graph.
 */
async function openCanvas(
  page: Page,
  name: string,
  extraNodes: Record<string, unknown>[] = [],
  startNodes: Record<string, unknown>[] = [startNodeOnly()],
): Promise<void> {
  const wfId = await createWorkflow(user.token, name);
  // New workflows are empty by design; seed a single StartNode so the canvas
  // has the starting point these build cases assume before adding more nodes.
  // B3 overrides the StartNode to point at the seeded CodeNode (pre-connected).
  await seedNodes(user.token, wfId, [...startNodes, ...extraNodes]);
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
}

/**
 * Maximise the canvas working area for the handle-drag cases: collapse the
 * right inspector so the source-handle on the right of a node is not occluded
 * by the inspector panel, then fit the view so both nodes + their handles are
 * fully on-screen.
 */
async function maximiseCanvas(page: Page): Promise<void> {
  const collapse = page.locator('[data-action="inspector-collapse"]');
  if (await collapse.count()) await collapse.click();
}

/**
 * Add a node via the LEFT Explorer node palette. The right-click "Add node…"
 * context-menu item was intentionally removed — node-adding now lives in the
 * palette. Open the Explorer ("Files" toolbar button), expand the "Nodes"
 * block (collapsed by default), then DOUBLE-CLICK the node card, which inserts
 * that node at the viewport center (NodeCard.onDoubleClick → store.addNode).
 */
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

/**
 * Right-click the pane to open the context menu, then activate the item with
 * `data-action=action` by focusing it + pressing Enter. Mouse-clicking these
 * Radix menu items is unstable in headless Chromium (the item is perpetually
 * "not stable"); keyboard activation is deterministic.
 */
async function contextMenuAction(
  page: Page,
  action: string,
  pos: { x: number; y: number } = { x: 30, y: 30 },
): Promise<void> {
  const pane = page.locator('[data-canvas-pane]');
  await pane.click({ button: 'right', position: pos });
  const item = page.locator(`[data-action="${action}"]`);
  await item.waitFor({ state: 'visible' });
  await item.focus();
  await page.keyboard.press('Enter');
}

/** Drag from a source node's source-handle to a target node's target-handle. */
async function dragConnect(
  page: Page,
  sourceType: string,
  targetType: string,
): Promise<void> {
  const source = page
    .locator('.react-flow__node', { has: page.locator(`[aria-label^="${sourceType}"]`) })
    .locator('.react-flow__handle.source');
  const target = page
    .locator('.react-flow__node', { has: page.locator(`[aria-label^="${targetType}"]`) })
    .locator('.react-flow__handle.target');
  const s = await source.boundingBox();
  const t = await target.boundingBox();
  if (!s || !t) throw new Error('handle bounding boxes not found');
  const sx = s.x + s.width / 2;
  const sy = s.y + s.height / 2;
  const tx = t.x + t.width / 2;
  const ty = t.y + t.height / 2;
  // xyflow's connection state machine reacts to pointer move/down/up. Hover
  // the handle first, press, drift through an intermediate point so a
  // connection-in-progress is registered, then release on the target.
  await page.mouse.move(sx, sy);
  await page.mouse.down();
  await page.mouse.move((sx + tx) / 2, (sy + ty) / 2, { steps: 8 });
  await page.mouse.move(tx, ty, { steps: 8 });
  await page.mouse.up();
}

test('B1 add multiple node types via the context menu', async ({ page }) => {
  await openCanvas(page, 'Acc Build Types');
  for (const t of ['CodeNode', 'PromptNode', 'ConditionNode', 'EndNode']) {
    await addNode(page, t);
  }
  // 5 nodes total (seeded Start + 4 added).
  await expect(page.locator('.react-flow__node')).toHaveCount(5);
  await screenshot(page, '10-all-node-types');
});

test('B2 connect two nodes by dragging handles → edge appears', async ({ page }) => {
  // Seed Start + a disconnected CodeNode at known, non-overlapping positions
  // (the spec allows seeding for the connect/disconnect cases — palette inserts
  // land at the viewport center and would stack/overlap handles). Collapse the
  // inspector so the source-handle on the right of a node is not occluded.
  await openCanvas(page, 'Acc Build Connect', [disconnectedCodeNode()]);
  await maximiseCanvas(page);
  await expect(page.locator('[aria-label^="CodeNode"]').first()).toBeVisible();

  await dragConnect(page, 'StartNode', 'CodeNode');

  // An edge is now rendered (re-derived from the source's `children[]`).
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);
  await screenshot(page, '11-edges-connected');
});

test('B3 delete an edge → persists across save + reload', async ({ page }) => {
  // Seed Start → Code ALREADY connected (the persisted baseline has the edge),
  // so deleting it is a real change that dirties the draft. B2 covers the
  // connect-by-drag path; B3's focus is delete-edge + persistence. We make the
  // Start point at the CodeNode and render an edge from the seeded `children[]`.
  const start = startNodeOnly();
  start.children = ['node_2'];
  await openCanvas(page, 'Acc Build EdgeDelete', [disconnectedCodeNode()], [start]);
  await maximiseCanvas(page);
  await expect(page.locator('[aria-label^="CodeNode"]').first()).toBeVisible();
  await expect(page.locator('.react-flow__edge')).toHaveCount(1);

  // Select the edge, then delete via the context menu's "Delete selected edge".
  await page.locator('.react-flow__edge').first().click({ force: true });
  await contextMenuAction(page, 'context-delete-edge');
  await expect(page.locator('.react-flow__edge')).toHaveCount(0);

  // Save → reload → edge stays deleted (Finding #4 regression guard).
  await page.locator('[data-action="canvas-save"]').click();
  await expect(page.getByText('Saved')).toBeVisible({ timeout: 10_000 });
  await page.reload();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
  await expect(page.locator('.react-flow__edge')).toHaveCount(0);
  await screenshot(page, '12-edge-delete-persist');
});

test('B4 delete a node via context menu', async ({ page }) => {
  await openCanvas(page, 'Acc Build NodeDelete');
  await addNode(page, 'CodeNode');
  await expect(page.locator('.react-flow__node')).toHaveCount(2);

  // Select the CodeNode then delete it via the context menu's "Delete node".
  await page.locator('[aria-label^="CodeNode"]').first().click();
  await contextMenuAction(page, 'context-delete-node');
  await expect(page.locator('.react-flow__node')).toHaveCount(1);
  await screenshot(page, '13-node-delete');
});
