import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

const session = new E2ECookieSession();
const unique = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
const workflowName = `Command acceptance ${unique}`;
const taskName = `Scheduled acceptance ${unique}`;
const deploymentName = `Deployment acceptance ${unique}`;
const deploymentSlug = `acceptance-${unique.toLowerCase().replace(/_/g, '-')}`;
const planTitle = `Plan acceptance ${unique}`;

test.setTimeout(1_200_000);

test.beforeAll(async () => {
  await session.register('command-matrix-e2e');
  await session.api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: 'langchain' }),
  });
});

test.beforeEach(async ({ context }: { context: BrowserContext }) => {
  await session.seed(context, 'en');
});

async function openNewChat(page: Page) {
  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-action="chat-new"]').click();
  const options = page.locator('[data-role="chat-composer-options-toggle"]');
  await options.click();
  await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
}

async function resumeActiveChat(page: Page) {
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({ timeout: 30_000 });
}

async function sendAndWait(page: Page, prompt: string, marker: string, timeout = 360_000) {
  const composer = page.locator('[data-role="agent-composer-input"]');
  await composer.fill(prompt);
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeEnabled();
  await page.locator('[data-action="agent-composer-send"]').click();
  await expect(
    page.locator('[data-message-role="assistant"]').filter({ hasText: marker }).last(),
  ).toBeVisible({ timeout });
  await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible({ timeout: 60_000 });
}

test('real /build create+execute, Workflow Sandbox, /task, /deployment, and /plan', async ({ page }) => {
  const pageErrors: string[] = [];
  page.on('pageerror', (error) => pageErrors.push(error.message));

  await openNewChat(page);
  await sendAndWait(page, [
    '/build Create a new workflow named', `"${workflowName}".`,
    'Build the smallest valid executable workflow with a StartNode connected to an EndNode.',
    'Use /data/workflow.json as the source file, validate it, import it to the canvas,',
    'then call run_workflow with an empty input object and wait for the execution result.',
    'Do not stop after creation or validation. Only after create_workflow, check_workflow,',
    'update_canvas, and run_workflow all succeed, reply exactly BUILD_EXEC_OK.',
  ].join(' '), 'BUILD_EXEC_OK', 480_000);

  for (const tool of ['create_workflow', 'check_workflow', 'update_canvas', 'run_workflow']) {
    await expect(page.getByText(new RegExp(tool)).last()).toBeVisible();
  }
  // Follow the exact product affordance returned by the tool instead of using
  // an out-of-band resource lookup.
  await page.getByRole('button', { name: /View workflow/i }).click();
  await expect(page.locator('[data-role="chat-workflow-viewer"]')).toBeVisible({ timeout: 60_000 });
  const [editorPage] = await Promise.all([
    page.context().waitForEvent('page'),
    page.getByRole('button', { name: /Open editor/i }).click(),
  ]);
  await editorPage.waitForLoadState('domcontentloaded');
  await expect(editorPage).toHaveURL(/\/workflow\/[^/]+$/, { timeout: 60_000 });
  // Browser regression for the previously reported empty-Sandbox failure.
  await expect(editorPage.getByText('Sandbox', { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await expect(editorPage.getByText(/Failed to load files/i)).toHaveCount(0);
  await editorPage.close();

  await resumeActiveChat(page);
  await sendAndWait(page, [
    '/task Call task_create_scheduled_run exactly once to create an enabled interval task named',
    `"${taskName}" for the existing workflow named "${workflowName}".`,
    'Use interval_seconds=3600, timezone="UTC", empty input_preset, mount_enabled=false,',
    'and in-app notifications for both succeeded and failed. Discover the exact workflow id first.',
    'After the tool succeeds, reply exactly TASK_CREATE_OK.',
  ].join(' '), 'TASK_CREATE_OK');
  await expect(page.getByText(/task_create_scheduled_run/).last()).toBeVisible();
  const tasksPage = await page.context().newPage();
  await tasksPage.goto('/tasks');
  await tasksPage.getByRole('tab', { name: /Scheduled run/i }).click();
  await expect(tasksPage.getByText(taskName, { exact: true })).toBeVisible({ timeout: 60_000 });
  await tasksPage.close();

  await resumeActiveChat(page);
  await sendAndWait(page, [
    '/deployment Call deployment_create exactly once to publish an API deployment named',
    `"${deploymentName}" with slug "${deploymentSlug}" for the existing workflow named "${workflowName}".`,
    'Use version_pin="head", rate_limit_qps=3, and discover the exact workflow id first.',
    'After the tool succeeds, reply exactly DEPLOYMENT_CREATE_OK. Do not repeat any credential.',
  ].join(' '), 'DEPLOYMENT_CREATE_OK');
  await expect(page.getByText(/deployment_create/).last()).toBeVisible();
  const deploymentsPage = await page.context().newPage();
  await deploymentsPage.goto('/deployments');
  await expect(deploymentsPage.getByText(deploymentName, { exact: true })).toBeVisible({ timeout: 60_000 });
  await deploymentsPage.close();

  // Continue in the same Chat: slash commands are independent Turns, while
  // the current workflow binding remains available throughout the journey.
  await resumeActiveChat(page);
  await sendAndWait(page, [
    '/plan Create and submit a static execution plan titled', `"${planTitle}".`,
    'Write valid JSON to /data/plans/acceptance.plan.json with exactly three nodes:',
    'start -> one subagent -> end. The subagent task must write the single line PLAN_NODE_OK',
    'to /data/plan-work/acceptance.txt and return that path. Use max_wall_time_seconds=300.',
    'Call create_execution_plan exactly once after writing the file.',
    'After the tool succeeds, reply exactly PLAN_CREATE_OK.',
  ].join(' '), 'PLAN_CREATE_OK', 480_000);
  await expect(page.getByText(/create_execution_plan/).last()).toBeVisible();
  const planCard = page.locator('[data-role="execution-plan-card"]').last();
  await expect(planCard).toBeVisible({ timeout: 60_000 });
  await expect(planCard).toHaveAttribute('data-plan-status', 'completed', { timeout: 360_000 });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-role="execution-plan-card"]').last()).toHaveAttribute(
    'data-plan-status',
    'completed',
    { timeout: 60_000 },
  );
  expect(pageErrors).toEqual([]);
});
