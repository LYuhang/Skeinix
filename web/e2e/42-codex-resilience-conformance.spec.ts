import { randomUUID } from 'node:crypto';

import { expect, test } from '@playwright/test';

import {
  assertOrderedDurableEvents,
  CodexResilienceFixture,
  terminalEvent,
  usageEvent,
} from './codex-resilience-fixture';

const fixture = new CodexResilienceFixture();
const RUN_COMPACTION = process.env.VIBECANVAS_CODEX_COMPACTION_E2E === '1';
const COMPACTION_TITLE = 'Codex emits a native compaction event under an opt-in pressure profile';
let initialized = false;

test.describe.configure({ mode: 'serial', timeout: 1_800_000 });

test.afterAll(() => {
  if (initialized) fixture.cleanup();
});
test.beforeEach(async ({ context }, testInfo) => {
  test.skip(
    testInfo.title === COMPACTION_TITLE && !RUN_COMPACTION,
    'Set VIBECANVAS_CODEX_COMPACTION_E2E=1 only with a model/profile whose native compaction threshold is known.',
  );
  if (!initialized) {
    await fixture.initialize();
    initialized = true;
  }
  await fixture.seed(context, 'en');
});

test('Codex streams, persists usage, stops, retries, reloads, and recovers', async ({
  page,
}, testInfo) => {
  const unique = randomUUID().replaceAll('-', '').slice(0, 16);
  const firstMarker = `CODEX_STREAM_${unique}`;
  const secondMarker = `CODEX_HISTORY_${unique}`;
  const retryMarker = `CODEX_RETRY_${unique}`;
  const recoveryMarker = `CODEX_RECOVERY_${unique}`;
  const continuedMarker = `CODEX_CONTINUED_${unique}`;

  await fixture.openNewChat(page);

  const first = await fixture.startTurn(page, [
    'Reply with five short numbered lines.',
    `The final line must contain exactly this marker: ${firstMarker}`,
  ].join('\n'));
  const firstEvents = await first.events;
  assertOrderedDurableEvents(firstEvents);
  expect(firstEvents.some((event) => event.name === 'CHAT_EVENT')).toBe(true);
  expect(terminalEvent(firstEvents)).toMatchObject({ name: 'done' });
  const firstUsage = usageEvent(firstEvents);
  expect(firstUsage, 'a real Codex Turn must expose provider usage').toBeTruthy();
  expect(firstUsage?.payload).toMatchObject({
    prompt_tokens: expect.any(Number),
    completion_tokens: expect.any(Number),
    total_tokens: expect.any(Number),
  });
  await expect(page.locator('[data-message-role="assistant"]').last()).toContainText(firstMarker, {
    timeout: 60_000,
  });

  const usageId = firstUsage?.id;
  expect(usageId).not.toBeNull();
  const usageReplay = await fixture.replay(first.chatId, first.turnId, Math.max(0, (usageId ?? 1) - 1));
  expect(['memory', 'database']).toContain(usageReplay.source);
  expect(usageEvent(usageReplay.events)?.payload).toEqual(firstUsage?.payload);
  expect(terminalEvent(usageReplay.events)).toMatchObject({ name: 'done' });

  const second = await fixture.startTurn(
    page,
    [
      `If the preceding assistant answer ended with ${firstMarker}, reply exactly ${secondMarker}.`,
      `Do not repeat ${firstMarker}.`,
    ].join('\n'),
  );
  const secondEvents = await second.events;
  assertOrderedDurableEvents(secondEvents);
  expect(secondEvents.some((event) => event.name === 'CHAT_EVENT')).toBe(true);
  expect(terminalEvent(secondEvents)).toMatchObject({ name: 'done' });
  await expect(page.locator('[data-message-role="assistant"]').last()).toContainText(secondMarker, {
    timeout: 60_000,
  });

  await page.reload({ waitUntil: 'domcontentloaded' });
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeEnabled({ timeout: 60_000 });
  await expect(page.locator('[data-message-role="assistant"]').filter({ hasText: firstMarker })).toHaveCount(1);
  await expect(page.locator('[data-message-role="assistant"]').filter({ hasText: secondMarker })).toHaveCount(1);

  const stoppable = await fixture.startTurn(page, [
    'Write 100 numbered lines. Each line must be a distinct, complete sentence about resilient',
    'stream processing. Do not use tools and do not omit, combine, or abbreviate any line.',
    `Line 100 must end with exactly this marker: ${retryMarker}`,
  ].join('\n'));
  await expect(page.locator('[data-action="agent-composer-stop"]')).toBeVisible({ timeout: 30_000 });
  await page.locator('[data-action="agent-composer-stop"]').click();
  const cancelledEvents = await stoppable.events;
  assertOrderedDurableEvents(cancelledEvents);
  expect(terminalEvent(cancelledEvents)).toMatchObject({
    name: 'error',
    payload: expect.objectContaining({ code: 'cancelled' }),
  });
  await expect(page.locator('[data-action="agent-composer-retry"]')).toBeVisible({ timeout: 60_000 });

  const retried = await fixture.startRetry(page);
  expect(retried.turnId).not.toBe(stoppable.turnId);
  const retriedEvents = await retried.events;
  assertOrderedDurableEvents(retriedEvents);
  expect(terminalEvent(retriedEvents)).toMatchObject({ name: 'done' });
  await expect(page.locator('[data-message-role="assistant"]').last()).toContainText(retryMarker, {
    timeout: 120_000,
  });

  const rejectedRead = await fixture.rejectOutOfWorkspaceRead(retried.chatId, '/etc/shadow');
  expect(rejectedRead.status).toBeGreaterThanOrEqual(400);
  expect(rejectedRead.status).toBeLessThan(500);
  expect(rejectedRead.body).not.toMatch(/postgres(?:ql)?:\/\//i);
  expect(rejectedRead.body).not.toMatch(
    /(?:authorization|proxy-authorization)\\?"?\s*:\s*\\?"?bearer\s+/i,
  );
  expect(rejectedRead.body).not.toMatch(/\bsk-[a-z0-9_-]{16,}\b/i);

  const recovery = await fixture.startTurn(page, `Reply exactly ${recoveryMarker}.`);
  const recoveryEvents = await recovery.events;
  assertOrderedDurableEvents(recoveryEvents);
  expect(terminalEvent(recoveryEvents)).toMatchObject({ name: 'done' });
  const recoveryWire = JSON.stringify(recoveryEvents);
  expect(recoveryWire).not.toMatch(/postgres(?:ql)?:\/\//i);
  expect(recoveryWire).not.toMatch(/(?:authorization|proxy-authorization)\\?"?\s*:\s*\\?"?bearer\s+/i);
  expect(recoveryWire).not.toMatch(/\bsk-[a-z0-9_-]{16,}\b/i);
  await expect(page.locator('[data-message-role="assistant"]').last()).toContainText(recoveryMarker, {
    timeout: 60_000,
  });

  const continued = await fixture.startTurn(page, `Reply exactly ${continuedMarker}.`);
  expect(terminalEvent(await continued.events)).toMatchObject({ name: 'done' });
  await expect(page.locator('[data-message-role="assistant"]').last()).toContainText(continuedMarker, {
    timeout: 60_000,
  });
  await page.screenshot({
    path: testInfo.outputPath('codex-resilience-conformance-final.png'),
    fullPage: true,
  });
});

test(COMPACTION_TITLE, async ({ page }) => {
  const turnCount = Number(process.env.VIBECANVAS_CODEX_COMPACTION_TURNS ?? '12');
  const charsPerTurn = Number(process.env.VIBECANVAS_CODEX_COMPACTION_CHARS_PER_TURN ?? '24000');
  expect(turnCount).toBeGreaterThanOrEqual(2);
  expect(charsPerTurn).toBeGreaterThanOrEqual(4_000);
  const entropy = Array.from({ length: Math.ceil(charsPerTurn / 33) }, (_, index) => (
    `${index.toString(36).padStart(6, '0')}-${randomUUID().replaceAll('-', '').slice(0, 24)}`
  )).join(' ').slice(0, charsPerTurn);

  await fixture.openNewChat(page);
  let compacted = false;
  for (let index = 0; index < turnCount && !compacted; index += 1) {
    const marker = `COMPACTION_PROBE_${index}`;
    const turn = await fixture.startTurn(page, [
      `Context pressure block ${index + 1}/${turnCount}:`,
      entropy,
      `Reply with exactly ${marker}.`,
    ].join('\n'));
    const events = await turn.events;
    expect(terminalEvent(events)).toMatchObject({ name: 'done' });
    compacted = events.some((event) => (
      event.name === 'TOOL_CALL'
      && JSON.stringify(event.payload).includes('context_compaction')
    ));
  }
  expect(
    compacted,
    'the configured native Codex pressure profile did not emit context_compaction; this is a blocker, not a pass',
  ).toBe(true);
});
