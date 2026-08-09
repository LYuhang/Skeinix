/**
 * E2E: agent chat SSE round-trip.
 *
 * Critical-journey G13 spec #3. Verifies the chat-stream loop in the
 * sidebar:
 *   - Send a turn from the composer
 *   - At least one assistant message bubble appears with non-empty text
 *     (signal-flow proves `CHAT_UPDATE` deltas arrived + the chat history
 *      hook invalidated after `done`)
 *
 * **Skip-by-default:** the backend agent calls a real LLM. Without
 * `AGENT_API_KEY` exported, the spec skips with a clear reason rather
 * than failing — agentless smoke tests are not yet supported (would
 * need an agent-mock mode in `vibecanvas-api`, tracked separately).
 *
 * We do NOT assert VIBE_ACTION application here: that depends on the
 * model deciding to call `vibe_workflow`, which is too prompt-sensitive
 * for a stable E2E. A separate spec can target it once we have a
 * deterministic agent mode.
 */
import { test, expect } from '@playwright/test';
import { seedAuthAndLocale } from './fixtures';

const HAS_AGENT_KEY = !!process.env.AGENT_API_KEY;
test.setTimeout(150_000);

test.beforeEach(async ({ context }) => {
  await seedAuthAndLocale(context);
});

test('streams an assistant reply from the agent', async ({ page }) => {
  test.skip(
    !HAS_AGENT_KEY,
    'AGENT_API_KEY not set — backend has no LLM creds. Skipping live SSE.',
  );

  await page.goto('/chat');
  await expect(page.locator('[data-role="agent-composer-input"]')).toBeVisible();
  await page.locator('[data-action="chat-new"]').click();

  const input = page.locator('[data-role="agent-composer-input"]');
  await expect(input).toBeEnabled({ timeout: 10_000 });
  const assistantMessages = page.locator('[data-message-role="assistant"]');
  const initialAssistantCount = await assistantMessages.count();
  await input.fill('Reply with the single word: OK');

  await page.locator('[data-action="agent-composer-send"]').click();

  // Assistant content is streamed into the canonical main Chat surface.
  await expect.poll(() => assistantMessages.count(), { timeout: 120_000 })
    .toBeGreaterThan(initialAssistantCount);
  const assistantBubble = assistantMessages.filter({ hasText: /\S/ }).last();
  await expect(assistantBubble).toBeVisible({ timeout: 120_000 });
});
