import { readFile } from 'node:fs/promises';
import path from 'node:path';
import process from 'node:process';
import { chromium } from '@playwright/test';

const mountPath = `/deployment-prefix-${Date.now().toString(36)}`;
const origin = 'https://deployment-prefix.invalid';
const assetDelayMs = Math.max(0, Number(process.env.SMOKE_ASSET_DELAY_MS ?? 0) || 0);
const distDir = path.resolve(process.cwd(), 'dist');
const indexPath = path.join(distDir, 'index.html');
const badAssetRequests = [];
const runtimeErrors = [];
const failedResponses = [];
const documentRequests = [];
const assetRequests = [];

function contentType(filePath) {
  if (filePath.endsWith('.js')) return 'text/javascript; charset=utf-8';
  if (filePath.endsWith('.css')) return 'text/css; charset=utf-8';
  if (filePath.endsWith('.svg')) return 'image/svg+xml';
  return 'application/octet-stream';
}

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(15_000);

  page.on('console', (message) => {
    if (
      message.type() === 'error' &&
      /modulepreload|dynamic import|useLocation|QueryClient|insertBefore|removeChild/i.test(message.text())
    ) {
      runtimeErrors.push(message.text());
    }
  });
  page.on('pageerror', (error) => {
    runtimeErrors.push(error.message);
  });
  page.on('response', (response) => {
    if (response.status() >= 400) failedResponses.push(`${response.status()} ${response.url()}`);
  });
  page.on('request', (request) => {
    const url = new URL(request.url());
    if (request.resourceType() === 'document') documentRequests.push(url.pathname);
    if (url.origin === origin && url.pathname.startsWith(`${mountPath}/assets/`)) {
      assetRequests.push(url.pathname);
    }
    if (
      url.origin === origin &&
      url.pathname.includes('/assets/') &&
      !url.pathname.startsWith(`${mountPath}/assets/`)
    ) {
      badAssetRequests.push(url.pathname);
    }
  });

  await page.context().route(`${origin}/**`, async (route) => {
    const url = new URL(route.request().url());
    if (
      route.request().resourceType() === 'document' &&
      (url.pathname === mountPath || url.pathname.startsWith(`${mountPath}/`))
    ) {
      // Serve the production artifact byte-for-byte. The application itself,
      // not a test-only proxy rewrite, must discover and retain the mount.
      await route.fulfill({
        status: 200,
        contentType: 'text/html',
        body: await readFile(indexPath, 'utf8'),
      });
      return;
    }
    if (url.pathname.startsWith(`${mountPath}/assets/`)) {
      const assetName = path.basename(url.pathname);
      const assetPath = path.join(distDir, 'assets', assetName);
      try {
        if (assetDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, assetDelayMs));
        }
        await route.fulfill({
          status: 200,
          contentType: contentType(assetPath),
          body: await readFile(assetPath),
        });
      } catch {
        await route.fulfill({ status: 404, body: 'not found' });
      }
      return;
    }
    if (url.pathname === '/favicon.svg') {
      await route.fulfill({ status: 204, body: '' });
      return;
    }
    if (url.pathname.startsWith(`${mountPath}/api/`)) {
      let status = 200;
      let payload = { items: [] };
      if (url.pathname.endsWith('/api/v1/auth/me')) {
        payload = {
          user_id: 'user_prefix_smoke',
          tenant_id: 'tenant_prefix_smoke',
          email: 'prefix-smoke@example.com',
          display_name: 'Prefix smoke',
        };
      } else if (url.pathname.endsWith('/api/v1/public-config')) {
        payload = { enable_test_user: false, agent_debug_view_enabled: false };
      } else if (url.pathname.endsWith('/api/v1/tasks/summary')) {
        payload = {
          active: 0,
          queued: 0,
          running: 0,
          cancelling: 0,
          failed: 0,
          finished: 0,
          cancelled: 0,
        };
      } else if (url.pathname.endsWith('/api/v1/tasks')) {
        payload = { items: [], total: 0, limit: 25, offset: 0 };
      } else if (
        url.pathname.endsWith('/api/v1/platform-management/overview')
        || url.pathname.endsWith('/api/v1/platform-management/context')
      ) {
        // The prefix smoke uses an ordinary account. A controlled permission
        // response still proves that the directly loaded route and its lazy
        // chunk resolved below the deployment mount instead of /management/.
        status = 403;
        payload = { detail: 'platform_management_forbidden' };
      } else if (url.pathname.endsWith('/api/v1/mcp-servers/platform')) {
        payload = {
          items: [{
            id: 'browser',
            name: 'Browser',
            description: 'Browser platform tools',
            activation: '/browser',
            activation_mode: 'command',
            runtime_types: ['langchain', 'codex'],
            tools: [],
          }],
        };
      }
      await route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(payload),
      });
      return;
    }
    await route.fulfill({ status: 404, body: 'unexpected request' });
  });

  await page.goto(`${origin}${mountPath}/login`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: /sign in|登录/i }).waitFor();
  const loginAssetCount = assetRequests.length;
  await page.evaluate(() => localStorage.setItem('vibecanvas.token', 'prefix-smoke-token'));
  await page.goto(`${origin}${mountPath}/management`, { waitUntil: 'domcontentloaded' });
  await page.getByText(/platform management is not available/i).waitFor();
  await page.goto(`${origin}${mountPath}/mcp-servers/platform/browser`, {
    waitUntil: 'domcontentloaded',
  });
  try {
    await page.getByRole('heading', { name: /Browser|浏览器/ }).waitFor();
  } catch (error) {
    const bodyText = (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 500);
    throw new Error(
      `direct platform MCP route did not render the expected service: ${bodyText}`,
      { cause: error },
    );
  }
  const assetsBeforeTasks = assetRequests.length;
  await page.goto(`${origin}${mountPath}/tasks`, { waitUntil: 'domcontentloaded' });
  await page.getByTestId('app-sidebar').waitFor();
  await page.getByRole('heading', { name: /^tasks?$|^任务$/i, level: 1 }).waitFor();
  const tasksAssetCount = assetRequests.length - assetsBeforeTasks;
  const sidebar = await page.getByTestId('app-sidebar').elementHandle();
  const documentCountBeforeClientNavigation = documentRequests.length;
  const assetsBeforeClientNavigation = assetRequests.length;
  for (const target of ['workspace', 'deployments', 'tasks']) {
    await page.locator(`a[href="${mountPath}/${target}"]`).click();
    await page.waitForURL(`${origin}${mountPath}/${target}`);
    await page.waitForTimeout(300);
    if (documentRequests.length !== documentCountBeforeClientNavigation) {
      throw new Error(`internal NavLink triggered a full document reload while opening ${target}`);
    }
    if (!sidebar || !(await page.evaluate((node) => (
      node.isConnected && node === document.querySelector('[data-testid="app-sidebar"]')
    ), sidebar))) {
      throw new Error(`application sidebar remounted while opening ${target}`);
    }
  }
  const clientNavigationAssetCount = assetRequests.length - assetsBeforeClientNavigation;

  await page.goBack({ waitUntil: 'domcontentloaded' });
  await page.waitForURL(`${origin}${mountPath}/deployments`);
  await page.goBack({ waitUntil: 'domcontentloaded' });
  await page.waitForURL(`${origin}${mountPath}/workspace`);
  await page.goForward({ waitUntil: 'domcontentloaded' });
  await page.waitForURL(`${origin}${mountPath}/deployments`);

  await page.context().addInitScript(() => {
    localStorage.setItem('vibecanvas.token', 'prefix-smoke-token');
  });
  const deepTab = await page.context().newPage();
  await deepTab.goto(`${origin}${mountPath}/mcp-servers/platform/browser`, {
    waitUntil: 'domcontentloaded',
  });
  await deepTab.getByRole('heading', { name: /Browser|浏览器/ }).waitFor();
  await deepTab.close();

  if (badAssetRequests.length > 0) {
    throw new Error(`runtime dropped the deployment prefix: ${badAssetRequests.join(', ')}`);
  }
  if (runtimeErrors.length > 0) {
    throw new Error(`browser reported deployment errors: ${runtimeErrors.join(' | ')}`);
  }
  const failedAssets = failedResponses.filter((failure) => failure.includes('/assets/'));
  if (failedAssets.length > 0) {
    throw new Error(`browser could not load deployment assets: ${failedAssets.join(' | ')}`);
  }

  process.stdout.write(
    `Deployment-prefix smoke passed at ${mountPath} ` +
      `(assets: login=${loginAssetCount}, tasks=${tasksAssetCount}, ` +
      `three-tab-nav=${clientNavigationAssetCount}).\n`,
  );
  if (process.env.SMOKE_TRACE_ASSETS === '1') {
    process.stdout.write(
      `Task navigation assets:\n${assetRequests
        .slice(assetsBeforeTasks, assetsBeforeClientNavigation)
        .map((requestPath) => `  ${path.basename(requestPath)}`)
        .join('\n')}\n`,
    );
  }
} finally {
  await browser.close();
}
