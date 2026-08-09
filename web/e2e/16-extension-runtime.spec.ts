/**
 * Opt-in, headed Chromium gate for the unpacked MV3 extension.
 *
 * Run under a display server after building `extension/dist`:
 *   VIBECANVAS_EXTENSION_E2E=1 xvfb-run -a pnpm exec playwright test \
 *     e2e/16-extension-runtime.spec.ts --workers=1
 *
 * Unlike the unit-level Chrome API fakes, this test exercises a real MV3
 * service worker, chrome.debugger attachments, content-script injection,
 * multiple top-level browser windows, and the extension's RUN_COMMAND protocol.
 */
import { createServer, type Server } from 'node:http';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium, expect, test, type BrowserContext, type Page, type Worker } from '@playwright/test';

const RUN_EXTENSION_E2E = process.env.VIBECANVAS_EXTENSION_E2E === '1';
const HERE = dirname(fileURLToPath(import.meta.url));
const EXTENSION_PATH = resolve(HERE, '../../extension/dist');

interface Observation {
  kind?: string;
  data?: Record<string, unknown>;
}

interface BrowserTopology {
  tabId: number;
  windowId: number;
}

function pageHtml(kind: 'primary' | 'detail' | 'other'): string {
  const content = {
    primary: ['Review Item 42', 'A product description ready for review.'],
    detail: ['Detail for Item 42', 'Extra evidence from the second tab.'],
    other: ['Out-of-scope window', 'This page belongs to another browser window.'],
  }[kind];
  const dark = kind === 'other';
  return `<!doctype html>
    <html lang="en"><head><meta charset="utf-8"><title>${content[0]}</title>
    <style>
      html { color-scheme: ${dark ? 'dark' : 'light'}; }
      body { margin: 0; min-height: 100vh; padding: 72px 48px; box-sizing: border-box;
        font: 16px/1.5 system-ui, sans-serif; color: ${dark ? '#f4f4f5' : '#18181b'};
        background: ${dark ? '#18181b' : '#fafafa'}; }
      main { max-width: 760px; margin: auto; }
    </style></head><body><main><h1>${content[0]}</h1><p>${content[1]}</p></main></body></html>`;
}

async function runtimeMessage<T = unknown>(page: Page, message: unknown): Promise<T> {
  return page.evaluate(async (payload) => {
    const runtime = (globalThis as unknown as {
      chrome: { runtime: { sendMessage: (message: unknown) => Promise<unknown> } };
    }).chrome.runtime;
    return await runtime.sendMessage(payload);
  }, message) as Promise<T>;
}

let commandSequence = 0;
async function runCommand(
  page: Page,
  cmd: string,
  args: Record<string, unknown>,
  chatId = 'chat-extension-e2e',
): Promise<Observation> {
  commandSequence += 1;
  const id = `extension-e2e-${commandSequence}`;
  const raw = await runtimeMessage<string>(page, {
    type: 'RUN_COMMAND',
    env: {
      v: 1,
      kind: 'command',
      id,
      channel: `chat:${chatId}`,
      transport: 'e2e:chromium',
      producer: 'e2e',
      data: {
        cmd,
        args: {
          command_id: id,
          turn_id: 'turn-extension-e2e',
          ...args,
        },
      },
    },
  });
  expect(typeof raw, `${cmd} must return an encoded observation`).toBe('string');
  return JSON.parse(raw) as Observation;
}

async function topologyFor(worker: Worker, url: string): Promise<BrowserTopology> {
  return worker.evaluate(async (targetUrl) => {
    const tabsApi = (globalThis as unknown as {
      chrome: {
        tabs: {
          query: (query: Record<string, unknown>) => Promise<Array<{ id?: number; windowId: number; url?: string }>>;
        };
      };
    }).chrome.tabs;
    const tabs = await tabsApi.query({});
    const tab = tabs.find((candidate) => candidate.url === targetUrl);
    if (typeof tab?.id !== 'number') throw new Error(`No Chrome tab found for ${targetUrl}`);
    return { tabId: tab.id, windowId: tab.windowId };
  }, url);
}

async function createTab(
  worker: Worker,
  windowId: number,
  url: string,
  active = false,
): Promise<number> {
  return worker.evaluate(async ({ targetWindowId, targetUrl, makeActive }) => {
    const tabsApi = (globalThis as unknown as {
      chrome: {
        tabs: {
          create: (input: Record<string, unknown>) => Promise<{ id?: number }>;
        };
      };
    }).chrome.tabs;
    const tab = await tabsApi.create({ windowId: targetWindowId, url: targetUrl, active: makeActive });
    if (typeof tab.id !== 'number') throw new Error('Chrome did not return a tab id');
    return tab.id;
  }, { targetWindowId: windowId, targetUrl: url, makeActive: active });
}

async function createWindow(worker: Worker, url: string): Promise<BrowserTopology> {
  return worker.evaluate(async (targetUrl) => {
    const windowsApi = (globalThis as unknown as {
      chrome: {
        windows: {
          create: (input: Record<string, unknown>) => Promise<{ id?: number; tabs?: Array<{ id?: number }> }>;
        };
      };
    }).chrome.windows;
    const created = await windowsApi.create({ url: targetUrl, focused: false, type: 'normal' });
    const tabId = created.tabs?.[0]?.id;
    if (typeof created.id !== 'number' || typeof tabId !== 'number') {
      throw new Error('Chrome did not return the created window topology');
    }
    return { windowId: created.id, tabId };
  }, url);
}

async function extensionState(worker: Worker): Promise<Record<string, unknown>> {
  return worker.evaluate(async () => {
    const chromeApi = (globalThis as unknown as {
      chrome: {
        storage: {
          session: { get: (keys: string[]) => Promise<Record<string, unknown>> };
        };
      };
    }).chrome;
    const stored = await chromeApi.storage.session.get([
      'controlledTabIds',
      'currentBrowserSession',
      'islandState',
    ]);
    return stored;
  });
}

async function waitForControlledTabs(worker: Worker, expected: number[]): Promise<void> {
  await expect.poll(async () => {
    const state = await extensionState(worker);
    return ((state.controlledTabIds as number[] | undefined) ?? []).slice().sort((a, b) => a - b);
  }).toEqual(expected.slice().sort((a, b) => a - b));
}

async function extensionStateFromPage(page: Page): Promise<Record<string, unknown>> {
  return page.evaluate(async () => {
    const storage = (globalThis as unknown as {
      chrome: {
        storage: {
          session: { get: (keys: string[]) => Promise<Record<string, unknown>> };
        };
      };
    }).chrome.storage.session;
    return await storage.get(['controlledTabIds', 'currentBrowserSession', 'islandState']);
  });
}

async function waitForControlledTabsFromPage(page: Page, expected: number[]): Promise<void> {
  await expect.poll(async () => {
    const state = await extensionStateFromPage(page);
    return ((state.controlledTabIds as number[] | undefined) ?? []).slice().sort((a, b) => a - b);
  }).toEqual(expected.slice().sort((a, b) => a - b));
}

test.describe('real MV3 extension runtime', () => {
  test.skip(!RUN_EXTENSION_E2E, 'Set VIBECANVAS_EXTENSION_E2E=1 and run under Xvfb/Chrome.');
  test.describe.configure({ mode: 'serial' });

  let server: Server;
  let serverBase = '';
  let context: BrowserContext;
  let worker: Worker;
  let profileDir = '';
  let primaryPage: Page;

  test.beforeAll(async () => {
    server = createServer((request, response) => {
      const path = request.url?.split('?')[0];
      const kind = path === '/detail' ? 'detail' : path === '/other' ? 'other' : 'primary';
      response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
      response.end(pageHtml(kind));
    });
    await new Promise<void>((resolveListen, rejectListen) => {
      server.once('error', rejectListen);
      server.listen(0, '127.0.0.1', resolveListen);
    });
    const address = server.address();
    if (!address || typeof address === 'string') throw new Error('Fixture server did not bind a TCP port');
    serverBase = `http://127.0.0.1:${address.port}`;

    profileDir = await mkdtemp(resolve(tmpdir(), 'skeinix-extension-e2e-'));
    context = await chromium.launchPersistentContext(profileDir, {
      headless: false,
      viewport: { width: 1280, height: 900 },
      args: [
        `--disable-extensions-except=${EXTENSION_PATH}`,
        `--load-extension=${EXTENSION_PATH}`,
        '--no-first-run',
        '--no-default-browser-check',
      ],
    });
    worker = context.serviceWorkers()[0] ?? await context.waitForEvent('serviceworker');
    primaryPage = context.pages()[0] ?? await context.newPage();
    await primaryPage.goto(`${serverBase}/primary`);
    await primaryPage.locator('#skeinix-island-host').waitFor({ state: 'attached' });
  });

  test.afterAll(async () => {
    await context?.close();
    await new Promise<void>((resolveClose) => server?.close(() => resolveClose()));
    if (profileDir) await rm(profileDir, { recursive: true, force: true });
  });

  test('scopes multiple controlled tabs to one window and durably releases them', async ({ browserName: _browserName }, testInfo) => {
    test.setTimeout(120_000);
    const extensionId = new URL(worker.url()).host;
    const primary = await topologyFor(worker, `${serverBase}/primary`);

    await worker.evaluate(async () => {
      const storage = (globalThis as unknown as {
        chrome: { storage: { local: { set: (value: Record<string, unknown>) => Promise<void> } } };
      }).chrome.storage.local;
      await storage.set({ lang: 'zh', theme: 'dark' });
    });

    const panelPage = await context.newPage();
    await panelPage.setViewportSize({ width: 400, height: 800 });
    await panelPage.goto(`chrome-extension://${extensionId}/sidepanel.html`);
    await expect(panelPage.locator('html')).toHaveAttribute('lang', 'zh-CN');
    await expect(panelPage.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(panelPage.locator('#status-title')).toContainText('需要登录');
    await testInfo.attach('extension-sidepanel-zh-dark', {
      body: await panelPage.screenshot({ animations: 'disabled' }),
      contentType: 'image/png',
    });

    await primaryPage.bringToFront();
    await runtimeMessage(panelPage, {
      type: 'SIDEPANEL_WINDOW',
      windowId: primary.windowId,
      panelContextId: 'extension-e2e-panel-a',
    });

    const started = await runCommand(panelPage, 'start_session', {
      target: 'current',
      browser_session_id: 'browser-session-e2e-1',
      session_generation: 1,
    });
    expect(started.data).toMatchObject({ ok: true, started: true });
    await waitForControlledTabs(worker, [primary.tabId]);
    await expect.poll(async () => (await extensionState(worker)).islandState).toMatchObject({
      controlled: true,
      kind: 'ready',
    });
    await testInfo.attach('extension-dynamic-island-controlled', {
      body: await primaryPage.screenshot({ animations: 'disabled' }),
      contentType: 'image/png',
    });

    const primaryRead = await runCommand(panelPage, 'read_text', { tab: primary.tabId });
    expect(primaryRead.data?.ok).toBe(true);
    expect(primaryRead.data?.text).toContain('Review Item 42');

    const detailUrl = `${serverBase}/detail`;
    const detailTabId = await createTab(worker, primary.windowId, detailUrl);
    await expect.poll(() => context.pages().some((page) => page.url() === detailUrl)).toBe(true);
    const detailPage = context.pages().find((page) => page.url() === detailUrl);
    if (!detailPage) throw new Error('Playwright did not observe the detail tab');
    await detailPage.waitForLoadState('domcontentloaded');
    const adopted = await runCommand(panelPage, 'use_tab', { tab: detailTabId });
    expect(adopted.data).toMatchObject({ ok: true, tab: detailTabId, controlled: true });
    await waitForControlledTabs(worker, [primary.tabId, detailTabId]);

    const detailRead = await runCommand(panelPage, 'read_text', { tab: detailTabId });
    expect(detailRead.data?.ok).toBe(true);
    expect(detailRead.data?.text).toContain('Extra evidence from the second tab');
    await detailPage.bringToFront();
    await detailPage.locator('#skeinix-island-host').waitFor({ state: 'attached' });
    await testInfo.attach('extension-dynamic-island-background-tab', {
      body: await detailPage.screenshot({ animations: 'disabled' }),
      contentType: 'image/png',
    });
    await primaryPage.bringToFront();

    const other = await createWindow(worker, `${serverBase}/other`);
    const listed = await runCommand(panelPage, 'list_open_tabs', {});
    const listedTabs = (listed.data?.tabs as Array<{ tab: number }> | undefined) ?? [];
    expect(listedTabs.map((tab) => tab.tab)).toEqual(expect.arrayContaining([primary.tabId, detailTabId]));
    expect(listedTabs.map((tab) => tab.tab)).not.toContain(other.tabId);

    const crossWindow = await runCommand(panelPage, 'use_tab', { tab: other.tabId });
    expect(crossWindow.data).toMatchObject({
      ok: false,
      error_code: 'tab_out_of_scope',
      not_executed: true,
    });
    await waitForControlledTabs(worker, [primary.tabId, detailTabId]);

    const bindingHere = await runtimeMessage<Record<string, unknown>>(panelPage, {
      type: 'GET_BINDING',
      windowId: primary.windowId,
    });
    const bindingElsewhere = await runtimeMessage<Record<string, unknown>>(panelPage, {
      type: 'GET_BINDING',
      windowId: other.windowId,
    });
    expect(bindingHere).toMatchObject({
      browser_control_chat_id: 'chat-extension-e2e',
      browser_control_available_here: true,
    });
    expect(bindingElsewhere).toMatchObject({
      browser_control_chat_id: 'chat-extension-e2e',
      browser_control_available_here: false,
    });

    await runtimeMessage(panelPage, { type: 'WS_CLOSED' });
    await expect.poll(async () => (await extensionState(worker)).islandState).toMatchObject({
      controlled: true,
      kind: 'disconnected',
    });
    await runtimeMessage(panelPage, { type: 'WS_OPEN' });
    await expect.poll(async () => (await extensionState(worker)).islandState).toMatchObject({
      controlled: true,
      kind: 'recovered',
    });

    await runtimeMessage(panelPage, { type: 'STOP' });
    await waitForControlledTabs(worker, [primary.tabId, detailTabId]);

    const released = await runCommand(panelPage, 'end_session', {
      browser_session_id: 'browser-session-e2e-1',
      session_generation: 1,
      reason: 'e2e_complete',
    });
    expect(released.data).toMatchObject({ ok: true, released: true });
    await waitForControlledTabs(worker, []);
    await expect.poll(async () => (await extensionState(worker)).islandState).toMatchObject({
      controlled: false,
    });
    await expect.poll(async () => (await extensionState(worker)).currentBrowserSession).toBeUndefined();

    const repeatedRelease = await runCommand(panelPage, 'end_session', {
      browser_session_id: 'browser-session-e2e-1',
      session_generation: 1,
      reason: 'e2e_repeat',
    });
    expect(repeatedRelease.data).toMatchObject({ ok: true, released: true });

    const afterRelease = await runCommand(panelPage, 'read_text', { tab: primary.tabId });
    expect(afterRelease.data).toMatchObject({
      ok: false,
      error_code: 'browser_session_released',
      not_executed: true,
    });

    const restarted = await runCommand(panelPage, 'start_session', {
      target: 'current',
      browser_session_id: 'browser-session-e2e-2',
      session_generation: 2,
    });
    expect(restarted.data).toMatchObject({ ok: true, started: true });
    const readopted = await runCommand(panelPage, 'use_tab', { tab: detailTabId });
    expect(readopted.data).toMatchObject({ ok: true, controlled: true });
    await waitForControlledTabs(worker, [primary.tabId, detailTabId]);

    const oldWorker = worker;
    const workerUrl = oldWorker.url();
    const cdp = await context.newCDPSession(panelPage);
    const targets = await cdp.send('Target.getTargets') as {
      targetInfos: Array<{ targetId: string; type: string; url: string }>;
    };
    const serviceWorkerTarget = targets.targetInfos.find(
      (target) => target.type === 'service_worker' && target.url === workerUrl,
    );
    if (!serviceWorkerTarget) throw new Error('Could not resolve the MV3 service-worker target');
    await oldWorker.evaluate(() => {
      (globalThis as unknown as { __skeinixE2eEpoch?: string }).__skeinixE2eEpoch = 'before-stop';
    });
    await cdp.send('ServiceWorker.enable');
    await cdp.send('ServiceWorker.stopAllWorkers');
    await runtimeMessage(panelPage, { type: 'GET_BINDING', windowId: primary.windowId });
    await expect.poll(async () => {
      return oldWorker.evaluate(
        () => (globalThis as unknown as { __skeinixE2eEpoch?: string }).__skeinixE2eEpoch,
      );
    }).toBeUndefined();
    await cdp.detach();
    await waitForControlledTabsFromPage(panelPage, [primary.tabId, detailTabId]);

    const resumedAfterWorkerRestart = await runCommand(panelPage, 'read_text', {
      tab: detailTabId,
      browser_session_id: 'browser-session-e2e-2',
      session_generation: 2,
    });
    expect(resumedAfterWorkerRestart).toMatchObject({
      data: { text: expect.stringContaining('Extra evidence from the second tab') },
    });

    // Real target_closed events: closing one tab preserves the remaining
    // controlled tab, then closing the last tab releases the whole session.
    await panelPage.evaluate(async (tabId) => {
      const tabsApi = (globalThis as unknown as {
        chrome: { tabs: { remove: (tabId: number) => Promise<void> } };
      }).chrome.tabs;
      await tabsApi.remove(tabId);
    }, primary.tabId);
    await waitForControlledTabsFromPage(panelPage, [detailTabId]);
    const remainingTabRead = await runCommand(panelPage, 'read_text', { tab: detailTabId });
    expect(remainingTabRead.data?.text).toContain('Extra evidence from the second tab');

    await panelPage.evaluate(async (tabId) => {
      const tabsApi = (globalThis as unknown as {
        chrome: { tabs: { remove: (tabId: number) => Promise<void> } };
      }).chrome.tabs;
      await tabsApi.remove(tabId);
    }, detailTabId);
    await waitForControlledTabsFromPage(panelPage, []);
    await expect.poll(async () => (await extensionStateFromPage(panelPage)).currentBrowserSession).toBeUndefined();
    await expect.poll(async () => (await extensionStateFromPage(panelPage)).islandState).toMatchObject({
      controlled: false,
    });

    const afterLastTabClosed = await runCommand(panelPage, 'read_text', { tab: detailTabId });
    expect(afterLastTabClosed.data).toMatchObject({
      ok: false,
      error_code: 'browser_session_released',
      not_executed: true,
    });
  });
});
