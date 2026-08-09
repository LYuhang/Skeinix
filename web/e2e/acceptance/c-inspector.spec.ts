/**
 * Inspector acceptance: per-type config editors and field cards.
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

function node(
  id: string,
  type: string,
  name: string,
  extra: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    node_id: id,
    node_name: name,
    node_type: type,
    node_description: '',
    input_fields: {},
    output_fields: {},
    node_config: {},
    children: [],
    __attributes__: { x: 0, y: 0 },
    ...extra,
  };
}

async function openSeeded(page: Page): Promise<string> {
  const wfId = await createWorkflow(user.token, 'Acc Inspector Flow');
  // Replace the auto-seeded Start with a richer graph including the types
  // C1 asserts. Build a structurally simple graph (validity is E's concern).
  await seedNodes(user.token, wfId, [
    node('node_1', 'StartNode', 'Start', {
      input_fields: { topic: { type: 'string', value: '', reference: '' } },
      children: ['node_2'],
      __attributes__: { x: 0, y: 0 },
    }),
    node('node_2', 'PromptNode', 'Ask', {
      node_config: { prompt_template: 'Hi {{topic}}', model_name: '' },
      children: ['node_3'],
      __attributes__: { x: 250, y: 0 },
    }),
    node('node_3', 'CodeNode', 'Code', {
      node_config: { programming_language: 'python', process_fn: 'def process_fn(inputs):\n    return {}' },
      children: ['node_4'],
      __attributes__: { x: 500, y: 0 },
    }),
    node('node_4', 'HTTPRequestNode', 'Fetch', {
      node_config: { method: 'GET', url: '' },
      children: ['node_5'],
      __attributes__: { x: 750, y: 0 },
    }),
    node('node_5', 'ConditionNode', 'Branch', {
      node_config: { conditions: [{ condition_name: 'others', condition_str: 'others', next_node_id: null }] },
      children: [],
      __attributes__: { x: 1000, y: 0 },
    }),
  ]);
  await page.goto(`/workflow/${wfId}`);
  await expect(page.locator('[data-action="canvas-save"]')).toBeVisible();
  await expect(page.locator('[aria-label^="StartNode"]').first()).toBeVisible({
    timeout: 15_000,
  });
  return wfId;
}

test('C1 per-type config editors render', async ({ page }) => {
  await openSeeded(page);

  // Prompt: model dropdown + template.
  await page.locator('[aria-label^="PromptNode"]').first().click();
  await expect(page.getByTestId('inspector-tab-node')).toBeVisible();
  await expect(
    page.getByTestId('cfg-prompt-model-input').or(page.getByTestId('cfg-prompt-model-select')),
  ).toBeVisible();
  await screenshot(page, '20-inspector-PromptNode');

  // Code: language + process_fn.
  await page.locator('[aria-label^="CodeNode"]').first().click();
  await expect(page.getByTestId('cfg-code-fn')).toBeVisible();
  await screenshot(page, '20-inspector-CodeNode');

  // HTTP: headers editor present (cfg-http-headers).
  await page.locator('[aria-label^="HTTPRequestNode"]').first().click();
  await expect(page.getByTestId('cfg-http-headers')).toBeVisible();
  await screenshot(page, '20-inspector-HTTPRequestNode');

  // Condition: condition editor present.
  await page.locator('[aria-label^="ConditionNode"]').first().click();
  await expect(page.getByTestId('cfg-condition')).toBeVisible();
  await screenshot(page, '20-inspector-ConditionNode');
});

test('C2 edit node name + description dirties the workflow', async ({ page }) => {
  await openSeeded(page);
  await page.locator('[aria-label^="CodeNode"]').first().click();

  const nameInput = page.locator('#node-name-node_3');
  await nameInput.fill('Renamed Code');
  await nameInput.blur();

  // Node card reflects the new name; Save becomes enabled (dirty).
  await expect(page.locator('.react-flow__node').filter({ hasText: 'Renamed Code' })).toBeVisible();
  await expect(page.locator('[data-action="canvas-save"]')).toBeEnabled();
  await screenshot(page, '21-inspector-edit');
});

test('C3 add input field card with type dropdown + add output field', async ({ page }) => {
  await openSeeded(page);
  await page.locator('[aria-label^="CodeNode"]').first().click();

  // Add an input field card.
  await page.getByTestId('add-field-input').click();
  // A new field card appears (field_1) with a type dropdown.
  await expect(page.getByTestId('field-card-field_1')).toBeVisible();
  await expect(page.getByTestId('field-type-field_1')).toBeVisible();

  // Add an output field card.
  await page.getByTestId('add-field-output').click();
  // Output section also gets a field_1 card (scoped within output editor).
  await expect(page.getByTestId('fields-editor-output').getByTestId('field-card-field_1')).toBeVisible();
  await screenshot(page, '22-fields-and-refs');
});

test('C4 inspector collapse/expand + Info tab', async ({ page }) => {
  await openSeeded(page);
  // Info is a NODE-scope tab now (Node / Run node / Info) — select a node first.
  await page.locator('[aria-label^="CodeNode"]').first().click();
  await page.getByTestId('inspector-tab-info').click();
  await screenshot(page, '23-inspector-info');

  // Collapse then expand.
  await page.locator('[data-action="inspector-collapse"]').click();
  const toggle = page.locator('[data-action="toggle-inspector"]');
  await expect(toggle).toHaveAttribute('aria-label', /toggle inspector/i);
  await toggle.click();
  await expect(page.locator('[data-action="inspector-collapse"]')).toBeVisible();
});
