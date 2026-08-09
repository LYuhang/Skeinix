import { expect, test, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';

const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/([^/]+)\/chats\/([^/]+)\/messages$/;

test.setTimeout(1_200_000);

async function setRuntime(session: E2ECookieSession, runtime: 'langchain' | 'codex') {
  await session.api('/api/v1/agent-runtime/settings', {
    method: 'PUT',
    body: JSON.stringify({ default_runtime_type: runtime }),
  });
}

async function openNewChat(page: Page) {
  await page.goto('/chat');
  const composer = page.locator('[data-role="agent-composer-input"]');
  await expect(composer).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-action="chat-new"]').click();
  await page.locator('[data-role="chat-composer-options-toggle"]').click();
  await expect(page.locator('[data-role="chat-approval-mode-select"]')).toHaveCount(0);
  await expect(composer).toBeEditable({ timeout: 30_000 });
}

test.describe('LangChain /plan every tool', () => {
  const session = new E2ECookieSession();
  const unique = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  const planTitle = `Plan acceptance ${unique}`;
  const planPath = `/data/plans/acceptance-${unique}.plan.json`;
  const outputPath = `/data/plan-work/acceptance-${unique}.txt`;
  let chatId = '';

  test.beforeAll(async () => {
    await session.register('command-plan-langchain');
    await setRuntime(session, 'langchain');
  });

  test.afterAll(async () => {
    if (!chatId) return;
    const bootstrap = await session.api('/api/v1/chats/bootstrap', {}, true);
    if (!bootstrap.ok) return;
    const scope = await bootstrap.json() as { carrier_scope_id: string };
    await session.api(
      `/api/v1/chat-scopes/${encodeURIComponent(scope.carrier_scope_id)}`
        + `/chats/${encodeURIComponent(chatId)}`,
      { method: 'DELETE' },
      true,
    );
  });

  test.beforeEach(async ({ context }: { context: BrowserContext }) => {
    await session.seed(context, 'en');
  });

  test('writes, submits, executes and previews create_execution_plan', async ({ page }) => {
    await openNewChat(page);
    const bootstrap = await session.api('/api/v1/chats/bootstrap').then((response) => (
      response.json()
    )) as { available_commands: string[] };
    expect(bootstrap.available_commands).toContain('plan');

    const composer = page.locator('[data-role="agent-composer-input"]');
    await composer.fill([
      `/plan Create one static execution plan titled "${planTitle}".`,
      `Write valid schema_version 1 JSON to ${planPath}.`,
      'It must contain exactly start -> work -> end.',
      `The work subagent title is "Write marker" and its task is exactly: Write the single`,
      `line PLAN_NODE_OK to ${outputPath}, then return that absolute path.`,
      'Set budgets.max_wall_time_seconds to 300. Do not add any other fields.',
      `Call create_execution_plan exactly once with plan_path "${planPath}".`,
      'Do not call any other command tool.',
    ].join(' '));

    const activities = page.locator('[data-tool-activity="true"]').filter({
      hasText: 'create_execution_plan',
    });
    const [response] = await Promise.all([
      page.waitForResponse((candidate) => (
        candidate.request().method() === 'POST'
          && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
      ), { timeout: 30_000 }),
      page.locator('[data-action="agent-composer-send"]').click(),
    ]);
    expect(response.ok()).toBe(true);
    const match = new URL(response.url()).pathname.match(MESSAGE_PATH);
    expect(match).not.toBeNull();
    chatId = match![2];

    await expect(activities).toHaveCount(1, { timeout: 120_000 });
    const activityToggle = activities.first().locator('[data-action="tool-activity-toggle"]');
    if (await activityToggle.getAttribute('aria-expanded') !== 'true') {
      await activityToggle.click();
    }
    const calls = activities.first().locator(
      '[data-role="tool-call"][data-tool-name="create_execution_plan"]',
    );
    await expect(calls).toHaveCount(1, { timeout: 30_000 });
    await expect(calls.first()).toHaveAttribute('data-tool-status', 'done', {
      timeout: 480_000,
    });
    const planButton = page.locator('[data-action="view-execution-plan"]').last();
    await expect(planButton).toBeVisible({ timeout: 60_000 });

    await expect.poll(async () => {
      const items = await session.api(
        `/api/v1/execution-plans?chat_id=${encodeURIComponent(chatId)}`,
      ).then((value) => value.json()) as Array<{ status: string }>;
      return items.length === 1 ? items[0].status : 'missing';
    }, { timeout: 480_000, intervals: [1_000, 2_000, 5_000] }).toBe('completed');
    const persistedPlans = await session.api(
      `/api/v1/execution-plans?chat_id=${encodeURIComponent(chatId)}`,
    ).then((value) => value.json()) as Array<{
      plan_id: string; plan_run_id: string; status: string; title: string;
    }>;
    expect(persistedPlans).toHaveLength(1);
    expect(persistedPlans[0]).toMatchObject({ title: planTitle, status: 'completed' });

    const run = await session.api(
      `/api/v1/execution-plan-runs/${encodeURIComponent(persistedPlans[0].plan_run_id)}`,
    ).then((value) => value.json()) as {
      status: string; progress: { result_ref?: string }; nodes: Array<{ status: string }>;
    };
    expect(run.status).toBe('completed');
    expect(run.nodes).toHaveLength(3);
    expect(run.nodes.every((node) => node.status === 'succeeded')).toBe(true);
    expect(run.progress.result_ref).toBe(
      `/data/plans/runs/${persistedPlans[0].plan_run_id}/results.json`,
    );

    await planButton.click();
    await expect(page.locator('[data-role="chat-preview-pane"]')).toBeVisible({ timeout: 60_000 });
    await expect(page.getByText(planTitle, { exact: true }).last()).toBeVisible({ timeout: 60_000 });

    await page.reload({ waitUntil: 'domcontentloaded' });
    const persistedActivity = page.locator('[data-tool-activity="true"]').filter({
      hasText: 'create_execution_plan',
    });
    await expect(persistedActivity).toHaveCount(1, { timeout: 60_000 });
    const persistedToggle = persistedActivity.locator('[data-action="tool-activity-toggle"]');
    if (await persistedToggle.getAttribute('aria-expanded') !== 'true') {
      await persistedToggle.click();
    }
    await expect(page.locator(
      '[data-role="tool-call"][data-tool-name="create_execution_plan"]'
        + '[data-tool-status="done"]',
    )).toHaveCount(1, { timeout: 60_000 });
    await expect(page.locator('[data-action="view-execution-plan"]')).toHaveCount(1);
  });
});

test.describe('Codex Plan isolation', () => {
  const session = new E2ECookieSession();

  test.beforeAll(async () => {
    await session.register('command-plan-codex');
    await setRuntime(session, 'codex');
  });

  test.beforeEach(async ({ context }: { context: BrowserContext }) => {
    await session.seed(context, 'en');
  });

  test('does not advertise or execute /plan', async ({ page }) => {
    const bootstrap = await session.api('/api/v1/chats/bootstrap').then((response) => (
      response.json()
    )) as { available_commands: string[] };
    expect(bootstrap.available_commands).not.toContain('plan');

    await page.goto('/chat');
    const composer = page.locator('[data-role="agent-composer-input"]');
    await expect(composer).toBeEditable({ timeout: 30_000 });
    await page.locator('[data-action="chat-new"]').click();
    await composer.fill('/plan This must be rejected before any Runtime or tool starts.');
    const activities = page.locator('[data-tool-activity="true"]');
    const [response] = await Promise.all([
      page.waitForResponse((candidate) => (
        candidate.request().method() === 'POST'
          && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
      ), { timeout: 30_000 }),
      page.locator('[data-action="agent-composer-send"]').click(),
    ]);
    expect(response.ok()).toBe(true);
    await expect(activities).toHaveCount(0);
    await expect(page.getByText(/available only with the LangChain Runtime/i)).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator('[data-action="agent-composer-send"]')).toBeVisible();
  });
});
