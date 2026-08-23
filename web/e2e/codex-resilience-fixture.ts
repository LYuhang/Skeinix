import { expect, type BrowserContext, type Page } from '@playwright/test';

import { E2ECookieSession } from './cookie-session';
import {
  provisionRealRuntime,
  type RealRuntimeProfile,
  selectRuntimeModel,
} from './real-runtime-profile';

const MESSAGE_PATH = /\/api\/v1\/chat-scopes\/[^/]+\/chats\/([^/]+)\/messages$/;

export type SseEvent = {
  id: number | null;
  name: string;
  payload: unknown;
};

export type RunningTurn = {
  chatId: string;
  turnId: string;
  events: Promise<SseEvent[]>;
};

function parsePayload(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

export function parseSse(body: string): SseEvent[] {
  return body
    .split(/\r?\n\r?\n/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => {
      let id: number | null = null;
      let name = 'message';
      const data: string[] = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('id:')) {
          const parsed = Number(line.slice(3).trim());
          id = Number.isFinite(parsed) ? parsed : null;
        } else if (line.startsWith('event:')) {
          name = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
          data.push(line.slice(5).trimStart());
        }
      }
      return { id, name, payload: parsePayload(data.join('\n')) };
    });
}

export function assertOrderedDurableEvents(events: SseEvent[]) {
  const durableIds = events.flatMap((event) => event.id === null ? [] : [event.id]);
  expect(durableIds.length).toBeGreaterThan(0);
  expect(new Set(durableIds).size).toBe(durableIds.length);
  expect(durableIds).toEqual([...durableIds].sort((left, right) => left - right));
}

export function terminalEvent(events: SseEvent[]) {
  return events.findLast((event) => event.name === 'done' || event.name === 'error');
}

export function usageEvent(events: SseEvent[]) {
  return events.findLast((event) => event.name === 'USAGE');
}

function workspaceScope(chatId: string) {
  return `__chatws_v2_${Buffer.from(chatId, 'utf8').toString('base64url')}`;
}

export class CodexResilienceFixture {
  readonly session = new E2ECookieSession();

  private runtimeProfile: RealRuntimeProfile | null = null;

  async initialize() {
    await this.session.register('codex-resilience-e2e');
    this.runtimeProfile = await provisionRealRuntime(this.session, 'codex');
  }

  cleanup() {
    this.runtimeProfile?.cleanup();
  }

  async seed(context: BrowserContext, locale = 'en') {
    await this.session.seed(context, locale);
  }

  async openNewChat(page: Page) {
    await page.goto('/chat', { waitUntil: 'domcontentloaded' });
    await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible({
      timeout: 30_000,
    });
    await page.locator('[data-action="chat-new"]').click();
    await expect(page.locator('[data-role="chat-model-select"]')).toBeEnabled({
      timeout: 30_000,
    });
    if (!this.runtimeProfile) throw new Error('Codex fixture is not initialized');
    await selectRuntimeModel(page, this.runtimeProfile);
  }

  async startTurn(page: Page, prompt: string): Promise<RunningTurn> {
    const composer = page.locator('[data-role="agent-composer-input"]');
    await expect(composer).toBeEnabled({ timeout: 30_000 });
    await composer.fill(prompt);
    const [response] = await Promise.all([
      page.waitForResponse((candidate) => (
        candidate.request().method() === 'POST'
        && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
      ), { timeout: 30_000 }),
      page.locator('[data-action="agent-composer-send"]').click(),
    ]);
    return this.turnFromResponse(response);
  }

  async startRetry(page: Page): Promise<RunningTurn> {
    const retry = page.locator('[data-action="agent-composer-retry"]');
    await expect(retry).toBeVisible({ timeout: 60_000 });
    const [response] = await Promise.all([
      page.waitForResponse((candidate) => (
        candidate.request().method() === 'POST'
        && MESSAGE_PATH.test(new URL(candidate.url()).pathname)
      ), { timeout: 30_000 }),
      retry.click(),
    ]);
    return this.turnFromResponse(response);
  }

  async replay(chatId: string, turnId: string, afterId = 0) {
    const events: SseEvent[] = [];
    let cursor = Math.max(0, afterId);
    let source: string | null = null;
    for (let attempt = 0; attempt < 5; attempt += 1) {
      const response = await this.session.api(
        `/api/v1/chats/${encodeURIComponent(chatId)}/turns/${encodeURIComponent(turnId)}/stream`,
        { headers: cursor > 0 ? { 'Last-Event-ID': String(cursor) } : {} },
      );
      source = response.headers.get('x-replay-source') ?? source;
      const replayed = parseSse(await response.text());
      events.push(...replayed);
      cursor = replayed.reduce(
        (latest, event) => event.id === null ? latest : Math.max(latest, event.id),
        cursor,
      );
      if (terminalEvent(events)) return { source, events };
      await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));
    }
    throw new Error(`Turn ${turnId} replay closed five times without a terminal event`);
  }

  async rejectOutOfWorkspaceRead(chatId: string, path: string) {
    const response = await this.session.api(
      `/api/v1/vfs/content?wf_id=${encodeURIComponent(workspaceScope(chatId))}`
        + `&path=${encodeURIComponent(path)}`,
      {},
      true,
    );
    return {
      status: response.status,
      body: await response.text(),
    };
  }

  private turnFromResponse(response: import('@playwright/test').Response): RunningTurn {
    const match = new URL(response.url()).pathname.match(MESSAGE_PATH);
    const chatId = match?.[1];
    const turnId = response.headers()['x-turn-id'];
    if (!chatId) throw new Error(`message response did not identify a Chat: ${response.url()}`);
    if (!turnId || !/^t_/.test(turnId)) {
      throw new Error(`message response did not identify a Turn: ${turnId ?? '<missing>'}`);
    }
    return {
      chatId,
      turnId,
      events: this.replay(chatId, turnId).then((result) => result.events),
    };
  }
}
